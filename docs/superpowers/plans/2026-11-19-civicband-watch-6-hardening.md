# CivicBand Watch — Milestone 6: Hardening

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce duplicate queue rows, suggest subreddit labels from accumulated text, alert on stale ingestion, and give the operator weekly and per-keyword visibility.

**Architecture:** Cross-post grouping is a nullable self-FK on `Thread` (`duplicate_of`). Duplicate collapse happens at ingest. A nightly command proposes `source="text_evidence"` subreddit labels without touching manual rows. Health and summary commands use Django’s email backend, which is Postmark in production.

**Tech Stack:** Django 5.2, django-rq, `django.core.mail`, pytest, factory-boy.

**Spec:** `docs/superpowers/specs/2026-11-19-civicband-watch-design.md`

## Global Constraints

- No new infrastructure. Commands run through the existing cron and worker.
- Text-evidence suggestions are proposals. Never overwrite `source="manual"` rows.
- Emails never include the f5bot feed URL or raw snippets. Summaries link to staff pages.
- All new tables/fields are additive `watch` migrations.
- The full test suite must pass with all optional features off.
- Run `uv run pytest`, `ruff`, and `mypy` before each commit.

## Review Focus

- Two bot cross-posts of the same story in different subreddits must produce one queue row and one “also posted in” list.
- Two genuinely different threads with the same title outside the 48-hour window must stay separate.
- The same author posting the same text twice in one thread must not inflate `mention_count`.
- A text-evidence suggestion must not overwrite a hand label, a mute, or a note.
- A stale-ingest email must not be sent repeatedly every hour.

---

### Task 1: Cross-post grouping

**Files:**
- Modify: `watch/models.py` (add `Thread.duplicate_of` self-FK null `SET_NULL` related `duplicates`), `watch/migrations/0004_thread_duplicate_of.py`
- Modify: `watch/ingest.py`, `watch/scoring.py`
- Test: `tests/watch/test_crossposts.py`

**Interfaces:**
- Produces:
  - `normalize_title(title: str) -> str` in `watch/ingest.py`: lower-case, strip URLs, punctuation, trailing ellipses, and the words `crosspost`, `x-post`, `repost`.
  - `group_crosspost(thread: Thread) -> Thread | None` returning the canonical thread when the new thread is a duplicate, else `None`.
- Rules:
  - Find a candidate thread with the same normalized title, a different subreddit, and `last_seen >= now - 48h`, excluding threads that are themselves duplicates.
  - Only queue-eligible threads may become canonical: `status=new`, or `status=snoozed` with `snoozed_until <= now`. Replied, dismissed, and backlog threads are terminal for grouping so a newly visible crosspost is not hidden under them.
  - If found, set `thread.duplicate_of = candidate`.
  - Do not group two threads from the same subreddit.
  - Do not group titles shorter than 20 characters or titles in `GENERIC_TITLES` (`daily discussion`, `weekly thread`, `megathread`, `questions thread`). This avoids collapsing routine recurring posts.
  - A mute, reply, dismiss, or backlog applied to the canonical after grouping may still hide both threads; that is accepted operator intent, not a reason to regroup.
- `queue_queryset` excludes `duplicate_of__isnull=False`.
- `TriageView` context adds `also_posted_in` = `thread.duplicates.select_related("subreddit")`.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.django_db
def test_crosspost_in_another_subreddit_is_grouped():
    canonical = ThreadFactory(
        title="Massive data center proposed",
        subreddit=SubredditFactory(name="sandiego"),
    )
    duplicate = ThreadFactory(
        title="Massive data center proposed!",
        subreddit=SubredditFactory(name="california"),
    )
    assert group_crosspost(duplicate) == canonical
    duplicate.refresh_from_db()
    assert duplicate.duplicate_of == canonical
    assert duplicate not in queue_queryset()


@pytest.mark.django_db
def test_same_title_outside_window_is_separate():
    ThreadFactory(
        title="City council delays the annual budget vote",
        subreddit=SubredditFactory(name="a"),
        last_seen=timezone.now() - timedelta(days=3),
    )
    new = ThreadFactory(
        title="City council delays the annual budget vote",
        subreddit=SubredditFactory(name="b"),
    )
    assert group_crosspost(new) is None


@pytest.mark.django_db
def test_same_subreddit_is_not_grouped():
    ThreadFactory(
        title="City council delays the annual budget vote",
        subreddit=SubredditFactory(name="a"),
    )
    new = ThreadFactory(
        title="City council delays the annual budget vote",
        subreddit=SubredditFactory(name="a"),
    )
    assert group_crosspost(new) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/watch/test_crossposts.py -q --no-cov`
Expected: FAIL because the field and helper do not exist.

- [ ] **Step 3: Implement grouping**

Call `group_crosspost(thread)` after creating a new thread in `ingest_page`. Do not regroup existing threads on every ingest; only when `thread` is newly created.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/watch/test_crossposts.py -q --no-cov`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add watch/models.py watch/migrations watch/ingest.py watch/scoring.py watch/views.py tests/watch/test_crossposts.py
git commit -m "feat(watch): group cross-posted stories"
```

---

### Task 2: Duplicate collapse within a thread

**Files:**
- Modify: `watch/ingest.py`
- Test: `tests/watch/test_duplicate_collapse.py`

**Interfaces:**
- Produces: `is_duplicate_mention(row: dict, thread: Thread) -> bool` returning true when an existing `Mention` in the same thread has the same `author` and `snippet_text` (case-insensitive, whitespace-collapsed).
- `ingest_page` increments `skipped` and does not create the mention or change `mention_count`.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.django_db
def test_same_author_same_text_in_thread_is_skipped():
    thread = ThreadFactory()
    MentionFactory(thread=thread, author="user1", snippet_text="Same text")
    row = parsing.parse_item(
        {
            "id": "F5BOT-alert-1-2",
            "url": "https://www.reddit.com/r/test/comments/abc/c/def",
            "title": "city council - Reddit Comments - Synthetic thread abc",
            "content_html": "Same text",
            "date_published": "2026-11-19T11:19:08-06:00",
            "tags": ["city council", "Reddit Comments"],
            "username": "user1",
        }
    )
    assert is_duplicate_mention(row, thread) is True
```

Use the anonymized page fixture and duplicate one item inside the page to exercise `ingest_page`:

```python
@pytest.mark.django_db
def test_ingest_collapses_identical_text():
    page = load("f5bot_page_1.json")
    page["items"].append(dict(page["items"][0]))
    page["items"][-1]["id"] = "F5BOT-alert-1-duplicate"
    seen, new, skipped = ingest_page(page)
    assert skipped == 1
    assert Mention.objects.filter(id="F5BOT-alert-1-duplicate").count() == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/watch/test_duplicate_collapse.py -q --no-cov`
Expected: FAIL because `is_duplicate_mention` does not exist.

- [ ] **Step 3: Implement the check**

Normalize with `" ".join(text.lower().split())`. Require a non-empty `author` and non-empty text; never collapse anonymous posts.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/watch/test_duplicate_collapse.py -q --no-cov`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add watch/ingest.py tests/watch/test_duplicate_collapse.py
git commit -m "feat(watch): collapse duplicate mentions within threads"
```

---

### Task 3: Text-evidence subreddit suggestions

**Files:**
- Create: `watch/evidence.py`
- Create: `watch/management/commands/suggest_watch_subreddits.py`
- Test: `tests/watch/test_evidence.py`

**Interfaces:**
- Produces:
  - `suggest_subreddits(*, days: int = 90, min_threads: int = 3, share: float = 0.7, dry_run: bool = False) -> dict[str, int]`
  - Returns `{"suggested": ..., "conflicts": ..., "unchanged": ...}`.
- Rules:
  - Consider `Subreddit` rows with no `place` and `source != "manual"`, plus rows whose current place disagrees with the evidence.
  - Aggregate distinct subject `ThreadPlace.place` per subreddit over the window.
  - Suggest only when one place has at least `min_threads` threads and at least `share` of the subreddit’s subject threads.
  - Set `Subreddit.place`, `kind="place"`, `source="text_evidence"`, `confidence="medium"`, `reviewed_at=None`, and a note such as `"text evidence: 8 of 10 subject threads"`.
  - If a manual row disagrees, count `conflicts` and do not write.
- Command options: `--days`, `--min-threads`, `--share`, `--dry-run`.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.django_db
def test_evidence_suggests_place_without_overwriting_manual():
    sub = SubredditFactory(name="localnews", source="gazetteer")
    place = PlaceFactory(name="Chico")
    for _ in range(4):
        thread = ThreadFactory(subreddit=sub)
        ThreadPlace.objects.create(
            thread=thread,
            place=place,
            role="subject",
            confidence="high",
            source="subreddit",
        )
    assert suggest_subreddits()["suggested"] == 1
    sub.refresh_from_db()
    assert sub.place == place
    assert sub.source == "text_evidence"


@pytest.mark.django_db
def test_evidence_does_not_overwrite_manual():
    sub = SubredditFactory(
        name="localnews", source="manual", place=PlaceFactory(name="Other")
    )
    place = PlaceFactory(name="Chico")
    for _ in range(4):
        ThreadPlace.objects.create(
            thread=ThreadFactory(subreddit=sub),
            place=place,
            role="subject",
            confidence="high",
            source="subreddit",
        )
    stats = suggest_subreddits()
    assert stats["conflicts"] == 1
    sub.refresh_from_db()
    assert sub.place.name == "Other"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/watch/test_evidence.py -q --no-cov`
Expected: FAIL because `watch.evidence` does not exist.

- [ ] **Step 3: Implement the aggregation and command**

Use `values("thread__subreddit", "place").annotate(threads=Count("thread", distinct=True))` and compute shares in Python. Skip muted subreddits.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/watch/test_evidence.py -q --no-cov`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add watch/evidence.py watch/management/commands/suggest_watch_subreddits.py tests/watch/test_evidence.py
git commit -m "feat(watch): suggest subreddit places from text evidence"
```

---

### Task 4: Stale-ingest health check and email

**Files:**
- Modify: `config/settings/base.py`, `.env.example`, `watch/apps.py`
- Create: `watch/health.py`, `watch/management/commands/check_watch_health.py`
- Create: `templates/admin/watch_index.html`, `watch/templatetags/watch_health.py`
- Test: `tests/watch/test_health.py`, `tests/watch/test_admin_index.py`

**Interfaces:**
- Produces settings:
  - `WATCH_ALERT_EMAIL = env.list("WATCH_ALERT_EMAIL", [])`
  - `WATCH_SUMMARY_EMAIL = env.list("WATCH_SUMMARY_EMAIL", [])`
  - `WATCH_STALE_HOURS = env.int("WATCH_STALE_HOURS", 6)`
- Produces:
  - `health_status(now=None) -> dict[str, object]` with `last_success`, `hours_since_success`, `alerts_today`, `stale`, `quiet`.
  - `send_stale_alert(status: dict) -> bool` returning whether an email was sent. Uses `django.core.mail.send_mail` and `django.core.cache` key `watch:stale-alert:<YYYY-MM-DD>` to send at most once per day.
  - `send_quiet_alert(status: dict) -> bool` using cache key `watch:quiet-alert:<YYYY-MM-DD>`. It sends only when quiet and not stale, so a stale day produces one stale email.
  - `format_last_success(value)` accepts either a `datetime` or an `IngestRun` and formats `finished_at`, or returns `"never"`.
- Stale if the last successful `IngestRun` is older than `WATCH_STALE_HOURS` or absent. Quiet if there are zero `new` mentions in the last 24 hours.
- Stale email subject: `"CivicBand Watch: ingest is stale"`. Body: last success time, hours since, pages/seen/new, and a link to `/admin/watch/ingestrun/`. Never include the feed URL or snippets.
- Quiet email subject: `"CivicBand Watch: no new alerts"`. Body: last success time, hours since, `alerts_today`, and the ingest-run admin link. Never include the feed URL or snippets.
- Command `check_watch_health` sends the stale email when stale, otherwise the quiet email when quiet, exits nonzero when stale or quiet, and prints nothing when healthy so hourly cron output does not mail.
- Admin index: set `admin.site.index_template = "admin/watch_index.html"` in `watch/apps.py` `ready()`. The template extends `admin/index.html`, calls `{% watch_health %}` before `{{ block.super }}`, and shows last successful run, hours since, and stale/quiet badges. The tag renders nothing for non-staff users.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.django_db
def test_health_reports_stale_and_sends_one_email(settings):
    settings.WATCH_ALERT_EMAIL = ["ops@example.com"]
    IngestRunFactory(finished_at=timezone.now() - timedelta(hours=8), new=10, error="")
    status = health_status()
    assert status["stale"] is True
    assert send_stale_alert(status) is True
    assert send_stale_alert(status) is False
    assert len(mail.outbox) == 1
    assert "f5bot.com" not in mail.outbox[0].body


@pytest.mark.django_db
def test_admin_index_shows_last_success(admin_client):
    IngestRunFactory(finished_at=timezone.now(), new=4, error="")
    response = admin_client.get(reverse("admin:index"))
    assert b"Last successful ingest" in response.content
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/watch/test_health.py -q --no-cov`
Expected: FAIL because `watch.health` does not exist.

- [ ] **Step 3: Implement health, email, and command**

Use `timezone.now()` and `timedelta`. Use `cache.add(key, True, timeout=86400)` before sending so concurrent cron runs cannot double-send.

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/watch/test_health.py -q --no-cov`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add watch/health.py watch/management/commands/check_watch_health.py config/settings/base.py .env.example tests/watch/test_health.py
git commit -m "feat(watch): alert when ingest is stale"
```

---

### Task 5: Weekly summary and per-keyword yield

**Files:**
- Create: `watch/management/commands/watch_weekly_summary.py`
- Create: `watch/management/commands/watch_keyword_report.py`
- Test: `tests/watch/test_reports.py`

**Interfaces:**
- `watch_weekly_summary` emails `WATCH_SUMMARY_EMAIL`:
  - Count of threads replied in the last 7 days.
  - Count of threads dismissed in the last 7 days.
  - Count of new uncovered subject places in the backlog.
  - A plain-text list of up to 20 replied threads with title, place, and `/triage/?thread=<uuid>` link.
  - Subject: `"CivicBand Watch: weekly summary"`.
  - Skips sending when the recipient list is empty.
- `watch_keyword_report` prints per f5bot keyword: mentions, distinct threads, threads with a covered subject place, and replied threads. Options `--days 30`, `--json`.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.django_db
def test_weekly_summary_sends_counts(settings):
    settings.WATCH_SUMMARY_EMAIL = ["ops@example.com"]
    ThreadFactory(status="replied", modified=timezone.now())
    call_command("watch_weekly_summary")
    assert "replied" in mail.outbox[0].body.lower()


@pytest.mark.django_db
def test_keyword_report_counts(capsys):
    MentionFactory(keyword="city council")
    call_command("watch_keyword_report")
    assert "city council" in capsys.readouterr().out
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/watch/test_reports.py -q --no-cov`
Expected: FAIL because the commands do not exist.

- [ ] **Step 3: Implement the commands**

Use `TruncDate` only if needed; otherwise filter on `modified__gte`. Keep the email text plain and free of snippets.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/watch/test_reports.py -q --no-cov`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add watch/management/commands/watch_weekly_summary.py watch/management/commands/watch_keyword_report.py tests/watch/test_reports.py
git commit -m "feat(watch): add weekly and keyword reports"
```

---

### Task 6: Deployment and operations documentation

**Files:**
- Modify: `docs/operations/civicband-watch-deploy.md`
- Modify: `README.md` (short pointer to the doc)

**Interfaces:**
- Produces documentation with:
  - First-run order: migrate, `load_watch_gazetteer --download`, `load_watch_governments --source-dir`, `load_watch_geonames --download`, optional `load_watch_wikidata`, `seed_watch_subreddits`, `link_watch_munis`, `classify_watch_subreddits`.
  - Cron entries for `ingest_f5bot` hourly, `check_watch_health` hourly after ingest, `suggest_watch_subreddits` nightly, `watch_weekly_summary` weekly.
  - Required env vars: `F5BOT_FEED_URL`, `WATCH_DATA_DIR`, `WATCH_ALERT_EMAIL`, `WATCH_SUMMARY_EMAIL`, optional `WATCH_LLM_*`.
  - Note that cron lives in `public-works`; this repo ships commands only.
  - How to run a backfill with `--max-pages 500`.
  - How to re-run enrichment for one thread from the admin.
  - A warning that the feed URL is a secret and must not be pasted into tickets or logs.

- [ ] **Step 1: Write the documentation**

Use the exact command names from milestones 1–5. Include a copy-paste cron block:

```cron
5 * * * * cd /home/deploy/civic-observer && docker compose -f docker-compose.production.yml run --rm web-blue python manage.py ingest_f5bot --max-pages 20
20 * * * * cd /home/deploy/civic-observer && docker compose -f docker-compose.production.yml run --rm web-blue python manage.py check_watch_health
30 3 * * * cd /home/deploy/civic-observer && docker compose -f docker-compose.production.yml run --rm web-blue python manage.py suggest_watch_subreddits
0 8 * * 1 cd /home/deploy/civic-observer && docker compose -f docker-compose.production.yml run --rm web-blue python manage.py watch_weekly_summary
```

- [ ] **Step 2: Commit**

```bash
git add docs/operations/civicband-watch-deploy.md README.md
git commit -m "docs(watch): add deployment and operations guide"
```

---

### Task 7: Milestone verification

Run:

```bash
uv run pytest tests/watch -q --no-cov
uv run pytest -q
uv run --group dev ruff check watch tests/watch
uv run --group dev mypy watch
```

Expected: all pass, including the coverage floor.

## Milestone 6 Acceptance

- Cross-posted stories from different subreddits collapse to one queue row and show the other subreddits.
- Genuine same-title threads outside 48 hours stay separate.
- Identical author/text duplicates within a thread do not inflate mention counts.
- Text-evidence suggestions never overwrite manual labels, mutes, or notes.
- Stale-ingest email sends at most once per day and contains no feed URL or snippets.
- Weekly summary and keyword report run.
- The deployment doc lists the exact first-run order and cron entries.
- `uv run pytest -q`, ruff, and mypy pass.
