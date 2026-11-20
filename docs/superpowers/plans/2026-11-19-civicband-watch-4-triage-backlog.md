# CivicBand Watch — Milestone 4: Triage and Backlog UI

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the operator a staff-only, keyboard-driven `/triage/` queue and a `/backlog/` view of uncovered places, both built with normal Django views, htmx partials, and the repo’s Tailwind styling.

**Architecture:** `watch/scoring.py` owns the queue ordering. `watch/views.py` has one page view and small POST action views per surface. Templates render a full page on normal requests and a partial on `HX-Request`. A tiny static JS file handles keyboard shortcuts and clipboard copy; all decisions still post through Django forms with CSRF.

**Tech Stack:** Django 5.2, htmx (already in `templates/base.html`), Alpine.js, Tailwind, pytest, factory-boy.

**Spec:** `docs/superpowers/specs/2026-11-19-civicband-watch-design.md`

## Global Constraints

- Every view is staff-only. Use `django.contrib.admin.views.decorators.staff_member_required`.
- All writes are POST with CSRF. Action views use `@require_POST`.
- Never render raw f5bot HTML with `|safe`. Render `snippet_text` escaped and highlight keywords with a template filter.
- Muting a subreddit or country hides its threads. Do not delete data.
- Backlog includes only places with no `Muni`, ranked by subject-place threads and distinct subreddits in the last 30 days.
- No new database table for the backlog; it is a query plus `PlaceNote`.
- Do not modify `meetings` or `municipalities`.
- Run `uv run pytest`, `ruff`, and `mypy` before each commit.

## Review Focus

- A non-staff logged-in user must not see thread snippets, meeting hits, or backlog data.
- A snoozed thread must reappear exactly when `snoozed_until` passes.
- A muted subreddit or country must disappear from the queue immediately, including after an htmx action.
- A thread whose subject place is uncovered must not show a misleading CivicBand hit.
- A malformed or missing meeting date must not break the queue; render “date unknown” rather than raising.

---

### Task 1: Queue scoring and backlog queries

**Files:**
- Create: `watch/scoring.py`
- Test: `tests/watch/test_scoring.py`

**Interfaces:**
- Produces:
  - `queue_queryset() -> QuerySet[Thread]`
  - `next_thread(after_id: uuid.UUID | None = None) -> Thread | None`
  - `backlog_queryset() -> QuerySet[Place]`
  - `thread_country(thread: Thread) -> str | None`
- `queue_queryset`:
  - Includes `status="new"` and `status="snoozed"` with `snoozed_until <= now`.
  - Excludes `subreddit.muted=True`.
  - Excludes a thread when `subreddit.country` is muted, or when it is empty/null and `subreddit.place.country` is muted (same precedence as `thread_country`).
  - Annotates `is_covered` = exists a `ThreadPlace` with `role="subject"` and `place.muni__isnull=False`.
  - Annotates `is_recent` = `last_seen >= now - 24h`.
  - Annotates `has_question` = title contains `?` or a mention snippet contains `?`.
  - Computes `score = is_covered*100 + is_recent*30 + has_question*15 + least(mention_count, 10)`.
  - Orders `-score`, `-last_seen`, `-id` for deterministic ties.
- `backlog_queryset`:
  - `Place.objects.filter(muni__isnull=True)`
  - Annotate `subject_threads` = distinct `ThreadPlace` threads with `role="subject"`, `thread.last_seen >= now - 30d`, and not muted.
  - Annotate `subject_subreddits` = distinct subreddits on those threads.
  - Filter `subject_threads__gt=0`, order `-subject_threads`, `-subject_subreddits`, then `population` descending with nulls last.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.django_db
def test_covered_recent_question_thread_ranks_first():
    covered = ThreadFactory(last_seen=timezone.now())
    ThreadPlace.objects.create(
        thread=covered,
        place=PlaceWithMuniFactory(),
        role="subject",
        confidence="high",
        source="subreddit",
    )
    MentionFactory(thread=covered, snippet_text="Will the council vote?")
    plain = ThreadFactory(last_seen=timezone.now() - timedelta(days=3))
    assert list(queue_queryset())[0] == covered


@pytest.mark.django_db
def test_muted_subreddit_and_country_are_hidden():
    sub = SubredditFactory(muted=True)
    hidden = ThreadFactory(subreddit=sub)
    country = SubredditFactory(country="IE")
    MutedCountry.objects.create(code="IE")
    hidden2 = ThreadFactory(subreddit=country)
    assert hidden not in queue_queryset()
    assert hidden2 not in queue_queryset()


@pytest.mark.django_db
def test_snoozed_thread_reappears_after_time():
    thread = ThreadFactory(
        status="snoozed", snoozed_until=timezone.now() - timedelta(minutes=1)
    )
    assert thread in queue_queryset()
    thread.snoozed_until = timezone.now() + timedelta(days=1)
    thread.save()
    assert thread not in queue_queryset()


@pytest.mark.django_db
def test_backlog_only_uncovered_subject_places():
    covered = PlaceWithMuniFactory()
    uncovered = PlaceFactory(name="Nowhere", muni=None)
    ThreadPlace.objects.create(
        thread=ThreadFactory(),
        place=covered,
        role="subject",
        confidence="high",
        source="subreddit",
    )
    ThreadPlace.objects.create(
        thread=ThreadFactory(),
        place=uncovered,
        role="subject",
        confidence="high",
        source="subreddit",
    )
    assert list(backlog_queryset()) == [uncovered]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/watch/test_scoring.py -q --no-cov`
Expected: FAIL because `watch.scoring` does not exist.

- [ ] **Step 3: Implement the queries**

Use `Exists`, `Case/When`, `ExpressionWrapper`, `IntegerField`, and `Least` from `django.db.models.functions`. Keep the weights as module constants `COVERED_WEIGHT = 100`, `RECENT_WEIGHT = 30`, `QUESTION_WEIGHT = 15`, `MENTION_CAP = 10`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/watch/test_scoring.py -q --no-cov`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add watch/scoring.py tests/watch/test_scoring.py
git commit -m "feat(watch): rank triage queue and backlog"
```

---

### Task 2: URLs, page views, and staff-only tests

**Files:**
- Create: `watch/views.py`, `watch/urls.py`
- Modify: `config/urls.py`
- Test: `tests/watch/test_views_access.py`

**Interfaces:**
- Produces:
  - `TriageView` GET `/triage/` with optional `?thread=<uuid>`. Context: `thread`, `queue_count`, `meeting_hits_by_place`, `subject_places`, `mentioned_places`, `previous_thread`, `next_thread`.
  - `BacklogView` GET `/backlog/`. Context: `places`, `total_threads`.
  - URL names `watch:triage`, `watch:backlog`. Tasks 4 and 5 add `watch:action`, `watch:place-correct`, and `watch:backlog-note`; Task 3 templates must not reverse those names until then.
- `TriageView` renders `watch/triage.html` on a full request and `watch/partials/triage_card.html` on `HX-Request`.
- `BacklogView` renders `watch/backlog.html` or `watch/partials/backlog_rows.html`.

- [ ] **Step 1: Write the failing access tests**

```python
@pytest.mark.django_db
def test_anonymous_is_redirected(client):
    assert client.get(reverse("watch:triage")).status_code == 302


@pytest.mark.django_db
def test_non_staff_is_rejected(client, user):
    client.force_login(user)
    response = client.get(reverse("watch:triage"))
    assert response.status_code in {302, 403}


@pytest.mark.django_db
def test_staff_sees_triage(admin_client):
    response = admin_client.get(reverse("watch:triage"))
    assert response.status_code == 200
    assert b"Triage" in response.content
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/watch/test_views_access.py -q --no-cov`
Expected: FAIL because the views and URLs do not exist.

- [ ] **Step 3: Implement the views and URLs**

Add `path("triage/", include("watch.urls"))` and `path("backlog/", include("watch.urls"))`? Prefer one include at root:

```python
path("", include("watch.urls")),
```

with `watch/urls.py` defining `triage/` and `backlog/`. Ensure no conflict with existing root paths.

Use `get_object_or_404(Thread, pk=thread_id)` for the optional id and fall back to `next_thread()`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/watch/test_views_access.py -q --no-cov`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add watch/views.py watch/urls.py config/urls.py tests/watch/test_views_access.py
git commit -m "feat(watch): add staff-only triage and backlog pages"
```

---

### Task 3: Triage template and meeting-hit partial

**Files:**
- Create: `templates/watch/triage.html`, `templates/watch/partials/triage_card.html`, `templates/watch/partials/meeting_hits.html`
- Create: `watch/templatetags/__init__.py`, `watch/templatetags/watch_tags.py`
- Test: `tests/watch/test_triage_template.py`

**Interfaces:**
- Produces:
  - `{% highlight_keywords text keywords %}` escaping text, then wrapping case-insensitive keyword matches in `<mark>`.
  - `{% citation hit %}` returning `[Meeting name (YYYY-MM-DD)](url)` as text.
- `triage` layout:
  - Header: title, subreddit, age, mention count, status.
  - Left column: each mention’s keyword, `snippet_text` highlighted, and an outbound Reddit link.
  - Right column: each subject place with its `MeetingHit` cards side by side; mentioned places as badges with a “Promote” button; a query input and “Run search” button; a “Copy citation” button per hit.
  - Action bar: Replied, Dismiss, Snooze, Backlog, Mute subreddit, Mute country, plus Previous/Next links using `previous_thread` and `next_thread`.
  - Keyboard hint line: `j/k next`, `r replied`, `d dismiss`, `s snooze`, `b backlog`, `p promote`, `m mute subreddit`, `x mute country`, `e edit query`, `c copy citation`.
  - Empty state: “No threads to triage.” Do not render an empty card or raise on `thread=None`.
- Dates render as `—` when `meeting_date` is null. Future hits show a “Future meeting” badge.

- [ ] **Step 1: Write the failing template tests**

```python
@pytest.mark.django_db
def test_triage_escapes_html_and_highlights_keyword(admin_client):
    thread = ThreadFactory()
    MentionFactory(
        thread=thread,
        keyword="city council",
        snippet_text="<script>alert(1)</script> city council",
    )
    response = admin_client.get(reverse("watch:triage"))
    content = response.content.decode()
    assert "<script>alert(1)</script>" not in content
    assert "&lt;script&gt;" in content
    assert "<mark>city council</mark>" in content


@pytest.mark.django_db
def test_triage_marks_future_agenda(admin_client):
    thread = ThreadFactory()
    place = PlaceWithMuniFactory()
    ThreadPlace.objects.create(
        thread=thread,
        place=place,
        role="subject",
        confidence="high",
        source="subreddit",
    )
    MeetingHitFactory(
        thread=thread,
        place=place,
        meeting_date=timezone.localdate() + timedelta(days=2),
    )
    response = admin_client.get(reverse("watch:triage"), {"thread": thread.id})
    assert b"Future meeting" in response.content
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/watch/test_triage_template.py -q --no-cov`
Expected: FAIL because the templates and tags do not exist.

- [ ] **Step 3: Implement the template and tags**

Use `django.utils.html.escape` in the filter, then replace escaped matches with `<mark>...</mark>`. Return `mark_safe` only on the fully escaped-and-marked string. Never pass feed HTML through.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/watch/test_triage_template.py -q --no-cov`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add templates/watch watch/templatetags tests/watch/test_triage_template.py
git commit -m "feat(watch): render triage queue with safe snippets"
```

---

### Task 4: Triage actions and corrections

**Files:**
- Create: `watch/forms.py`
- Modify: `watch/views.py`, `watch/urls.py`, `watch/lookup.py`
- Test: `tests/watch/test_triage_actions.py`

**Interfaces:**
- Produces:
  - `TriageActionForm` with `action` choice field and optional `days` int (1–90), `place_id` UUID, `country` char(2), `query` char(500).
  - `POST /triage/<uuid:thread_id>/action/` returning the next thread partial on `HX-Request`, else a redirect to `watch:triage`.
  - `POST /triage/<uuid:thread_id>/place/` for corrections: `place_id`, optional `matched_text`, `role`.
- Actions:
  - `replied`: `status="replied"`.
  - `dismiss`: `status="dismissed"`.
  - `snooze`: `status="snoozed"`, `snoozed_until=now + days` (default 7).
  - `backlog`: `status="backlog"`.
  - `mute_subreddit`: set `subreddit.muted=True`; leave the thread status unchanged.
  - `mute_country`: `MutedCountry.objects.get_or_create(code=thread_country(thread))`; leave thread unchanged.
  - `promote`: `ThreadPlace.update_or_create(thread, place, defaults={"role":"subject","confidence":"high","source":"manual"})`.
  - `edit_query`: set `ThreadPlace.query_override=query` for `place_id`; if `place_id` is omitted and the thread has exactly one subject place, use that place; otherwise return `400`. Then `refresh_thread_hits(thread)`.
- Corrections:
  - `ThreadPlace.update_or_create` with `source="manual"`, `role` from the form.
  - If `subreddit.source != "manual"`, set `subreddit.place=place`, `subreddit.source="manual"`, `subreddit.reviewed_at=now`.
  - If `matched_text` is provided, create a `PlaceAlias` with `kind="nickname"`, `source="manual"` for the corrected place.
- `build_query` must use `ThreadPlace.query_override` when present.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.django_db
def test_replied_action_persists(admin_client):
    thread = ThreadFactory()
    admin_client.post(reverse("watch:action", args=[thread.id]), {"action": "replied"})
    thread.refresh_from_db()
    assert thread.status == "replied"


@pytest.mark.django_db
def test_snooze_sets_future_timestamp(admin_client):
    thread = ThreadFactory()
    admin_client.post(
        reverse("watch:action", args=[thread.id]), {"action": "snooze", "days": 3}
    )
    thread.refresh_from_db()
    assert thread.status == "snoozed"
    assert thread.snoozed_until > timezone.now() + timedelta(days=2)


@pytest.mark.django_db
def test_mute_country_hides_thread(admin_client):
    thread = ThreadFactory(subreddit=SubredditFactory(country="IE"))
    admin_client.post(
        reverse("watch:action", args=[thread.id]), {"action": "mute_country"}
    )
    assert MutedCountry.objects.filter(code="IE").exists()
    assert thread not in queue_queryset()


@pytest.mark.django_db
def test_correction_sets_manual_thread_place(admin_client):
    thread = ThreadFactory()
    place = PlaceFactory()
    admin_client.post(
        reverse("watch:place-correct", args=[thread.id]),
        {"place_id": place.id, "role": "subject", "matched_text": "Chico"},
    )
    assert ThreadPlace.objects.get(thread=thread, place=place).source == "manual"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/watch/test_triage_actions.py -q --no-cov`
Expected: FAIL because the forms and views do not exist.

- [ ] **Step 3: Implement the form, actions, and correction view**

Validate `action` with `forms.ChoiceField`. Return `HttpResponseBadRequest` for an unknown action or a missing required field. Never mutate data on GET.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/watch/test_triage_actions.py -q --no-cov`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add watch/forms.py watch/views.py watch/urls.py watch/lookup.py tests/watch/test_triage_actions.py
git commit -m "feat(watch): persist triage actions and corrections"
```

---

### Task 5: Backlog view, notes, and ranking

**Files:**
- Create: `templates/watch/backlog.html`, `templates/watch/partials/backlog_rows.html`
- Modify: `watch/views.py`, `watch/urls.py`
- Create: `watch/forms.py` additions: `PlaceNoteForm`
- Test: `tests/watch/test_backlog.py`

**Interfaces:**
- Produces:
  - `BacklogView` context: `places` with `subject_threads`, `subject_subreddits`, `population`, `notes`, and `sample_threads` (first 3 subject threads).
  - `POST /backlog/<uuid:place_id>/note/` creates a `PlaceNote` and returns the refreshed row partial.
- Backlog row columns: place name/state/type, population, subject thread count, subreddit count, sample thread titles, note form, and the status select.
- Empty state: “No uncovered places yet.”

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.django_db
def test_backlog_lists_only_uncovered_and_ranks(admin_client):
    busy = PlaceFactory(name="Busy", muni=None)
    quiet = PlaceFactory(name="Quiet", muni=None)
    for _ in range(2):
        ThreadPlace.objects.create(
            thread=ThreadFactory(),
            place=busy,
            role="subject",
            confidence="high",
            source="subreddit",
        )
    ThreadPlace.objects.create(
        thread=ThreadFactory(),
        place=quiet,
        role="subject",
        confidence="high",
        source="subreddit",
    )
    response = admin_client.get(reverse("watch:backlog"))
    names = [p.name for p in response.context["places"]]
    assert names == ["Busy", "Quiet"]


@pytest.mark.django_db
def test_backlog_note_is_created(admin_client):
    place = PlaceFactory(muni=None)
    admin_client.post(
        reverse("watch:backlog-note", args=[place.id]),
        {"text": "Worth adding", "status": "open"},
    )
    assert place.notes.get().text == "Worth adding"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/watch/test_backlog.py -q --no-cov`
Expected: FAIL because the backlog view does not exist.

- [ ] **Step 3: Implement the backlog view and templates**

Prefetch notes and sample threads to avoid N+1 queries. Use `Prefetch("thread_places", queryset=..., to_attr="sample_thread_places")`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/watch/test_backlog.py -q --no-cov`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add templates/watch watch/views.py watch/urls.py watch/forms.py tests/watch/test_backlog.py
git commit -m "feat(watch): add backlog view and place notes"
```

---

### Task 6: Keyboard shortcuts, copy citation, and staff navigation

**Files:**
- Create: `frontend/js/watch-triage.js`
- Modify: `templates/watch/triage.html`, `templates/base.html`
- Test: `tests/watch/test_triage_js.py`

**Interfaces:**
- Produces: a deferred script that:
  - Listens for `keydown` on `document`.
  - Ignores events when the target is `INPUT`, `TEXTAREA`, `SELECT`, or `[contenteditable]`.
  - Maps `j`/`k` to next/previous thread links, `r/d/s/b` to action forms, `p` to the first promote button, `m`/`x` to mute buttons, `e` to focus the query input, and `c` to the first copy-citation button.
  - Copies citation text with `navigator.clipboard.writeText` and falls back to a hidden textarea + `document.execCommand("copy")`.
- `templates/base.html` adds staff-only “Triage” and “Backlog” links in the desktop and mobile account menus.

- [ ] **Step 1: Write the failing static test**

```python
from pathlib import Path


def test_triage_script_has_shortcuts_and_copy():
    text = Path("frontend/js/watch-triage.js").read_text()
    for key in ["j", "k", "r", "d", "s", "b", "p", "m", "x", "e", "c"]:
        assert f'"{key}"' in text
    assert "navigator.clipboard.writeText" in text
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/watch/test_triage_js.py -q --no-cov`
Expected: FAIL because the script does not exist.

- [ ] **Step 3: Implement the script and navigation**

Load the script in `triage.html` with `{% load static %}<script src="{% static 'js/watch-triage.js' %}" defer></script>`. The repo serves `frontend/` through `STATICFILES_DIRS`, so `frontend/js/watch-triage.js` is available as `js/watch-triage.js`.

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/watch/test_triage_js.py -q --no-cov`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/js/watch-triage.js templates/watch/triage.html templates/base.html tests/watch/test_triage_js.py
git commit -m "feat(watch): add triage keyboard shortcuts and navigation"
```

---

### Task 7: Thread admin filters and actions

**Files:**
- Modify: `watch/admin.py`
- Test: `tests/watch/test_thread_admin.py`

**Interfaces:**
- `ThreadAdmin` gains:
  - `list_filter`: `status`, `subreddit__kind`, a custom `CoveredFilter` (covered/uncovered subject place), a custom `UsFilter` (US/non-US), `subreddit__muted`, and `last_seen`.
  - `search_fields`: `title`, `mentions__snippet_text`, `mentions__keyword`, `subreddit__name`.
  - Actions: `mark_replied`, `mark_dismissed`, `snooze_seven_days`, `send_to_backlog`, `mute_subreddits`, `rerun_enrichment`.
- `CoveredFilter` uses `Exists(ThreadPlace.objects.filter(thread=OuterRef("pk"), role="subject", place__muni__isnull=False))`.
- `UsFilter` tests `subreddit__country="US"` and `subreddit__place__country="US"`; non-US is the negation.
- `rerun_enrichment` enqueues `enrich_thread` for each selected thread and reports the enqueued count.
- `mute_subreddits` sets `Subreddit.muted=True` for distinct subreddits on the selected threads.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.django_db
def test_thread_admin_covered_filter(admin_client):
    covered = ThreadFactory()
    ThreadPlace.objects.create(
        thread=covered,
        place=PlaceWithMuniFactory(),
        role="subject",
        confidence="high",
        source="subreddit",
    )
    uncovered = ThreadFactory()
    ThreadPlace.objects.create(
        thread=uncovered,
        place=PlaceFactory(muni=None),
        role="subject",
        confidence="high",
        source="subreddit",
    )
    response = admin_client.get(
        reverse("admin:watch_thread_changelist"), {"covered": "yes"}
    )
    assert list(response.context["cl"].queryset) == [covered]


@pytest.mark.django_db
def test_thread_admin_actions(admin_client):
    thread = ThreadFactory()
    admin_client.post(
        reverse("admin:watch_thread_changelist"),
        {"action": "mark_replied", "_selected_action": [thread.pk]},
    )
    thread.refresh_from_db()
    assert thread.status == "replied"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/watch/test_thread_admin.py -q --no-cov`
Expected: FAIL because the filters/actions do not exist.

- [ ] **Step 3: Implement the filters and actions**

Use `admin.SimpleListFilter` for `CoveredFilter` and `UsFilter`; use `@admin.action(description=...)` for each action. Do not enqueue jobs when Redis is unavailable without reporting the failure.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/watch/test_thread_admin.py -q --no-cov`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add watch/admin.py tests/watch/test_thread_admin.py
git commit -m "feat(watch): add thread admin filters and actions"
```

---

### Task 8: Milestone verification

Run:

```bash
uv run pytest tests/watch -q --no-cov
uv run --group dev ruff check watch tests/watch
uv run --group dev mypy watch
```

Expected: all pass.

## Milestone 4 Acceptance

- Anonymous and non-staff users cannot see triage or backlog data.
- Every queue action persists; `j/k` navigation and `c` copy work without JavaScript errors.
- Muting a subreddit or country hides its threads immediately.
- A correction sets a manual `ThreadPlace` and updates a non-manual subreddit label.
- The backlog lists only uncovered places, ranked by subject-place threads and distinct subreddits.
- Snippets are escaped and keyword highlighting is server-rendered.
- Missing meeting dates render safely.
- `uv run pytest -q`, ruff, and mypy pass.
