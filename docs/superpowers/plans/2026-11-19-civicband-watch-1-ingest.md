# CivicBand Watch — Milestone 1: App Skeleton and Ingest

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a staff-only `watch` app to civic-observer with the complete data model and an hourly f5bot JSON-feed ingester that is idempotent, paged, lock-protected, and recorded in `IngestRun`.

**Architecture:** A new Django app owns all watch tables. The stdlib parser from `_civic_band_watch/f5bot_ingest.py` is ported unchanged into `watch/parsing.py`; a Django service turns parsed items into `Mention` and `Thread` rows; a management command wraps the service with a Redis lock. Enrichment is an RQ job stub that later milestones fill in, so the ingest boundary is stable from day one.

**Tech Stack:** Django 5.2, PostgreSQL 17, django-rq, Redis, httpx, pytest, factory-boy, ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-11-19-civicband-watch-design.md`

## Global Constraints

- Staff-only. Nothing posts to Reddit or anywhere else, and nothing calls the Reddit API.
- The f5bot JSON feed is the only source. Read its URL from `F5BOT_FEED_URL` through `environs`; never commit it and never write it to logs, admin messages, or `IngestRun`.
- Migrations only add tables. Never alter `meetings` or `municipalities` tables or the out-of-band BM25 index.
- Every place resolves to a `Place` row with a namespaced `canonical_key`. No free-text place creation in this milestone.
- No new infrastructure. Use the existing Postgres, Redis, django-rq worker, and cron.
- LLM code paths are absent in this milestone.
- civic-observer is public. The raw `_civic_band_watch/alert-json-feed.json` must not be committed: it contains real usernames/content and its feed URLs contain the f5bot access hash. Commit only anonymized fixtures.
- Python 3.14, `uv run` for tools, ruff line length 88, pytest with the existing PostgreSQL test database.

## Review Focus

- A feed page containing an id already in `Mention` must stop paging without fetching another page.
- A feed item repeated inside one page must not create a duplicate `Mention` or inflate `Thread.mention_count`.
- A malformed item (missing id, blank content, non-UTC date) must be skipped or normalized without aborting the run.
- The feed URL secret must never appear in `IngestRun.source`, exception text, logs, or admin output.
- A second ingest of the same fixture must create zero mentions and zero threads.

---

## Data Model Contract

All files below live in `watch/models.py`. Every mutable table uses `TimeStampedModel` from `django-model-utils` unless noted. All choices are nested `models.TextChoices`.

| Model | Fields and constraints |
|---|---|
| `Thread` | UUID pk; `key` char(255) unique; `subreddit` FK `Subreddit` null, `SET_NULL`, related `threads`; `title` text blank; `first_seen`, `last_seen` datetime; `mention_count` positive int default 0; `status` choices `new/replied/dismissed/snoozed/backlog` default `new`; `snoozed_until` datetime null; `notes` text blank. Indexes `(status, last_seen)`, `(subreddit, last_seen)`. Ordering `-last_seen`. |
| `Mention` | char(255) pk (f5bot id); `thread` FK `Thread` CASCADE related `mentions`; `alert_num` bigint null; `url` URLField(1000); `site` char(32) default `reddit`; `kind` choices `post/comment/other`; `source_tag` char(255) blank; `keyword` char(255) blank, indexed; `subreddit_name` char(255) blank; `post_id`, `comment_id` char(64) blank; `post_title` text blank; `snippet_text`, `snippet_html` text blank; `highlights` JSON list default list; `author` char(255) blank; `grp` char(255) blank; `published_at` datetime null indexed; `raw_json` JSON dict; `ingested_at` auto_now_add. Index `(thread, published_at)`, `(keyword, published_at)`. Ordering `-published_at`. |
| `Subreddit` | char(255) pk (lower-case name); `kind` choices `place/region/state/non_us/general/ambiguous`; `place` FK `Place` null; `state` char(10) blank; `country` country field null; `confidence` choices `high/medium/low` blank; `source` choices `manual/wikidata/gazetteer/text_evidence/llm` blank; `muted` bool default false; `reviewed_at` datetime null; `note` text blank. Indexes `kind`, `source`, `muted`. |
| `Place` | UUID pk; `canonical_key` char(255) unique; `geoid` char(20) null; `name` char(255) indexed; `place_type` choices `state/county/county_subdivision/city/town/village/cdp/school_district/canadian_city/country`; `state` char(10) blank indexed; `country` country field default `US`; `county` self-FK null `SET_NULL` related `places_in_county`; `latitude`, `longitude` float null; `population` positive int null indexed; `is_government` bool default false indexed; `muni` FK `municipalities.Muni` null `SET_NULL` related `watch_places`; `muni_link_status` choices `unlinked/auto/confirmed/no_match` default `unlinked`; `muni_reviewed_at` datetime null. Unique constraint `(geoid, place_type)` where `geoid is not null`. Indexes `(place_type, state)`, `(is_government, state)`. Ordering `name`. |
| `PlaceAlias` | bigint pk; `place` FK `Place` CASCADE related `aliases`; `key` char(255) indexed; `kind` choices `name/name_state/name_state_name/name_city/name_county/nickname/variant/subreddit`; `source` choices `gazetteer/geonames/wikidata/manual`; unique `(place, key, kind)`. |
| `PlaceMention` | bigint pk; `mention` FK `Mention` CASCADE related `place_mentions`; `place` FK `Place` CASCADE related `mentions`; `matched_text` char(255); `method` choices `body_pattern/gazetteer_phrase/llm/manual`; `confidence` choices `high/medium/low`; `is_subject` bool default false; unique `(mention, place)`. |
| `ThreadPlace` | bigint pk; `thread` FK `Thread` CASCADE related `thread_places`; `place` FK `Place` CASCADE related `thread_places`; `role` choices `subject/mentioned`; `confidence` choices `high/medium/low`; `source` choices `subreddit/post/comment/manual`; `query_override` char(500) blank; unique `(thread, place)`. Indexes `(place, role)`, `(thread, role)`. |
| `MeetingHit` | bigint pk; `thread` FK `Thread` CASCADE related `meeting_hits`; `place` FK `Place` CASCADE related `meeting_hits`; `page` FK `meetings.MeetingPage` `DO_NOTHING`, `db_constraint=False`, related `+`; `query` text blank; `query_hash` char(64); `rank` float null; `snippet` text blank; `meeting_name` char(255) blank; `meeting_date` date null; `document_type` char(32) blank; `civic_band_url` URLField(1000) blank; `thumbnail_url` URLField(1000) blank; unique `(thread, place, page, query_hash)`; indexes `(thread, place)`, `(place, meeting_date)`. Ordering `meeting_date`, `meeting_name`. |
| `IngestRun` | bigint pk; `started_at` default `timezone.now`; `finished_at` datetime null; `source` char(255) blank; `pages`, `seen`, `new`, `skipped` positive int default 0; `error` text blank. Ordering `-started_at`. |
| `PlaceNote` | bigint pk; `place` FK `Place` CASCADE related `notes`; `text` text; `status` choices `open/maybe/rejected/added` default `open`. |
| `MutedCountry` | char(2) pk (ISO country code); `created` datetime auto_now_add. |

`Mention` is effectively immutable after ingest. Do not add `save()` side effects that rewrite parsed fields.

---

### Task 1: Create the `watch` app, settings, dependencies, and test package

**Files:**
- Create: `watch/__init__.py`, `watch/apps.py`, `watch/migrations/__init__.py`
- Create: `tests/watch/__init__.py`
- Modify: `config/settings/base.py`
- Modify: `pyproject.toml`
- Modify: `.env.example`

**Interfaces:**
- Produces: `watch` in `INSTALLED_APPS`; `F5BOT_FEED_URL`, `WATCH_DATA_DIR`, `WATCH_INGEST_LOCK_SECONDS` settings; `openpyxl` dependency for milestone 2; `--cov=watch` in pytest addopts.

- [ ] **Step 1: Write the failing test**

Create `tests/watch/test_app.py`:

```python
import pytest
from django.apps import apps
from django.conf import settings


def test_watch_app_is_installed():
    assert apps.is_installed("watch")


def test_watch_settings_exist():
    assert hasattr(settings, "F5BOT_FEED_URL")
    assert hasattr(settings, "WATCH_DATA_DIR")
    assert settings.WATCH_INGEST_LOCK_SECONDS >= 60
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/watch/test_app.py -q --no-cov`
Expected: FAIL because `watch` is not installed.

- [ ] **Step 3: Create the app and settings**

Create `watch/__init__.py` empty and `watch/apps.py`:

```python
from django.apps import AppConfig


class WatchConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "watch"
    verbose_name = "CivicBand Watch"
```

Add `"watch"` to `LOCAL_APPS` in `config/settings/base.py`. Add near the notification settings:

```python
# CivicBand Watch
F5BOT_FEED_URL = env.str("F5BOT_FEED_URL", "")
WATCH_DATA_DIR = env.str("WATCH_DATA_DIR", str(BASE_DIR / ".cache" / "watch"))
WATCH_INGEST_LOCK_SECONDS = env.int("WATCH_INGEST_LOCK_SECONDS", 3600)
```

Add to `.env.example` under a new `--- CivicBand Watch ---` heading:

```text
F5BOT_FEED_URL=
WATCH_DATA_DIR=.cache/watch
WATCH_INGEST_LOCK_SECONDS=3600
```

Add `openpyxl>=3.1` to `dependencies` in `pyproject.toml` (used by milestone 2) and `--cov=watch` to `addopts`.

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/watch/test_app.py -q --no-cov`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add watch tests/watch pyproject.toml config/settings/base.py .env.example
git commit -m "feat(watch): scaffold staff-only watch app"
```

---

### Task 2: Models, initial migration, and shared factories

**Files:**
- Create: `watch/models.py`, `watch/migrations/0001_initial.py`
- Modify: `tests/factories.py`
- Test: `tests/watch/test_models.py`, `tests/watch/test_constraints.py`

**Interfaces:**
- Produces: every model in the Data Model Contract; factories `SubredditFactory`, `PlaceFactory`, `PlaceWithMuniFactory`, `ThreadFactory`, `MentionFactory`, `ThreadPlaceFactory`, `MeetingHitFactory`, `IngestRunFactory`.
- `PlaceFactory` defaults: `canonical_key=factory.Sequence(lambda n: f"us:place:test{n:07d}")`, `name="Testville"`, `place_type="city"`, `state="CA"`, `country="US"`.
- `PlaceWithMuniFactory` subclasses `PlaceFactory` and sets `muni=SubFactory(MuniFactory)`, `muni_link_status="auto"`.
- `SubredditFactory` defaults: `name=factory.Sequence(lambda n: f"sub{n}")`, `kind="general"`, `confidence="low"`, `source="gazetteer"`.
- `ThreadFactory` defaults: `key=factory.Sequence(lambda n: f"reddit:test/{n}")`, `title="Test thread"`, `first_seen`/`last_seen` = `timezone.now()`.
- `MentionFactory` defaults: `id=factory.Sequence(lambda n: f"F5BOT-alert-1-{n}")`, `url="https://www.reddit.com/r/test/comments/abc/"`, `kind="post"`, `site="reddit"`, `thread=SubFactory(ThreadFactory)`, `published_at=timezone.now()`.
- `ThreadPlaceFactory` defaults: `thread=SubFactory(ThreadFactory)`, `place=SubFactory(PlaceFactory)`, `role="subject"`, `confidence="high"`, `source="subreddit"`.
- `IngestRunFactory` defaults: `started_at=timezone.now()`, `finished_at=timezone.now()`, `source="test"`, `pages=1`, `seen=0`, `new=0`, `skipped=0`, `error=""`.

- [ ] **Step 1: Write the failing model tests**

Create `tests/watch/test_models.py` with assertions for:

```python
@pytest.mark.django_db
def test_thread_key_is_unique():
    ThreadFactory(key="reddit:test/1")
    with pytest.raises(IntegrityError):
        ThreadFactory(key="reddit:test/1")


@pytest.mark.django_db
def test_mention_id_is_primary_key():
    mention = MentionFactory(id="F5BOT-alert-1-1")
    assert Mention.objects.get(pk="F5BOT-alert-1-1") == mention


@pytest.mark.django_db
def test_place_geoid_unique_per_type_not_globally():
    PlaceFactory(geoid="1234567", canonical_key="us:place:1234567", place_type="city")
    PlaceFactory(
        geoid="1234567", canonical_key="us:school:1234567", place_type="school_district"
    )
```

Create `tests/watch/test_constraints.py` asserting the duplicate `(geoid, city)` raises `IntegrityError`, duplicate `ThreadPlace(thread, place)` raises `IntegrityError`, and `MeetingHit` accepts a `page_id` that does not exist because `db_constraint=False`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/watch/test_models.py tests/watch/test_constraints.py -q --no-cov`
Expected: FAIL because `watch.models` and factories do not exist.

- [ ] **Step 3: Implement the models and factories**

Write `watch/models.py` exactly to the Data Model Contract. Use `models.UniqueConstraint(..., condition=Q(geoid__isnull=False), name="watch_place_unique_geoid_type")`.

Add factories to `tests/factories.py` using the defaults above. `MeetingHitFactory` should create `thread`, `place`, and set `page_id="page-1"`, `query_hash="0" * 64`, `meeting_date=date(2024, 1, 15)`; it must not create a `MeetingPage` by default.

Generate the migration:

```bash
uv run python manage.py makemigrations watch
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/watch/test_models.py tests/watch/test_constraints.py -q --no-cov`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add watch/models.py watch/migrations tests/factories.py tests/watch/test_models.py tests/watch/test_constraints.py
git commit -m "feat(watch): add watch data model"
```

---

### Task 3: Port the f5bot parser and anonymize the feed fixture

**Files:**
- Create: `watch/parsing.py`
- Create: `tests/watch/fixtures/f5bot_page_1.json`, `tests/watch/fixtures/f5bot_page_2.json`
- Test: `tests/watch/test_parsing.py`

**Interfaces:**
- Produces:
  - `to_utc_iso(value: str | None) -> str | None`
  - `html_to_text(fragment: str) -> str`
  - `strip_title_prefix(title: str, keyword: str | None, source_tag: str | None) -> str`
  - `classify_site(host: str) -> str`
  - `parse_item(item: dict) -> dict` with the same keys as the supplied parser.
  - Constants `REDDIT_RE`, `STRONG_RE`, `TAG_RE`, `ALERT_NUM_RE`.

- [ ] **Step 1: Create the anonymized fixture**

Write a one-off script (do not commit the raw feed) that reads `_civic_band_watch/alert-json-feed.json`, splits the 88 items into 50 and 38, and writes the two fixture files with these transformations:

- Replace `username` with `user0001`, `user0002`, … in order.
- Replace each item’s `title` with `{keyword} - {source_tag} - Synthetic thread {post_id}`.
- Replace `content_html` with `Synthetic <strong>{keyword}</strong> alert for thread {post_id}.`
- Keep `id`, `url`, `date_published`, `group`, and `tags` unchanged.
- Remove `feed_url`, `_prev_url`, and `_processing_time_seconds` from the page object.
- Set `next_url` to `"fixture:page-2"` in page 1 and `null` in page 2.
- Keep `version`, `title`, `home_page_url`, and `description`.

Assert the script output has 88 items total, 69 thread keys, one `reddit:losangeles/1xzzze7` thread with 16 mentions, and no value containing `a=` or `h=`.

- [ ] **Step 2: Write the failing parser tests**

Create `tests/watch/test_parsing.py` porting the supplied tests with synthetic items:

```python
import json
from pathlib import Path

from watch import parsing

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_comment_item():
    page = json.loads((FIXTURES / "f5bot_page_1.json").read_text())
    item = page["items"][0]
    row = parsing.parse_item(item)
    assert row["site"] == "reddit"
    assert row["kind"] == "comment"
    assert row["thread_key"].startswith("reddit:")
    assert row["published_at"].endswith("+00:00")


def test_entities_unescaped():
    row = parsing.parse_item(
        {
            "id": "F5BOT-alert-1-1",
            "url": "https://www.reddit.com/r/treelaw/comments/abc/c/def",
            "title": "planning commission - Reddit Comments - Synthetic thread abc",
            "content_html": "&gt; trees &quot;in violation of a Beverly Hills <strong>Planning Commission</strong>",
            "date_published": "2026-11-19T11:19:08-06:00",
            "tags": ["planning commission", "Reddit Comments"],
        }
    )
    assert row["snippet_text"].startswith('> trees "in violation of a Beverly Hills')
    assert json.loads(row["highlights"]) == ["planning commission"]


def test_post_and_canonical_comment_urls():
    base = {
        "id": "F5BOT-alert-1-2",
        "title": "city council - Reddit Posts - Synthetic",
        "content_html": "x",
        "date_published": "2026-11-19T11:19:08-06:00",
        "tags": ["city council", "Reddit Posts"],
    }
    post = parsing.parse_item(
        dict(base, url="https://www.reddit.com/r/ChicoCA/comments/abc/planning_thing/")
    )
    assert (post["subreddit"], post["post_id"], post["comment_id"], post["kind"]) == (
        "chicoca",
        "abc",
        None,
        "post",
    )
    comment = parsing.parse_item(
        dict(
            base,
            tags=["city council", "Reddit Comments"],
            url="https://www.reddit.com/r/Seattle/comments/abc/slug/def123/",
        )
    )
    assert (comment["post_id"], comment["comment_id"]) == ("abc", "def123")


def test_hn_item():
    row = parsing.parse_item(
        {
            "id": "F5BOT-alert-1-3",
            "url": "https://news.ycombinator.com/item?id=4242",
            "title": "city council - Hacker News Comments - Synthetic",
            "content_html": "x",
            "date_published": "2026-11-19T11:19:08-06:00",
            "tags": ["city council", "Hacker News Comments"],
        }
    )
    assert (row["site"], row["thread_key"], row["subreddit"]) == (
        "hackernews",
        "hn:4242",
        None,
    )


def test_title_without_expected_prefix():
    assert parsing.strip_title_prefix("Just a title", "x", "y") == "Just a title"
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/watch/test_parsing.py -q --no-cov`
Expected: FAIL because `watch.parsing` does not exist.

- [ ] **Step 4: Port the parser**

Copy the parsing functions and regexes from `_civic_band_watch/f5bot_ingest.py` into `watch/parsing.py` unchanged. Do not copy `SCHEMA`, `connect`, `upsert_items`, `load_subreddits`, `fetch_json`, `poll_feed`, or `main`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/watch/test_parsing.py -q --no-cov`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add watch/parsing.py tests/watch/test_parsing.py tests/watch/fixtures
git commit -m "feat(watch): port f5bot parser with anonymized fixtures"
```

---

### Task 4: Ingest service

**Files:**
- Create: `watch/ingest.py`
- Test: `tests/watch/test_ingest.py`

**Interfaces:**
- Consumes: `watch.parsing.parse_item`, models from Task 2.
- Produces:
  - `@dataclass(frozen=True) class IngestStats: pages: int; seen: int; new: int; skipped: int`
  - `fetch_json(url: str, *, client: httpx.Client | None = None) -> dict`
  - `ingest_page(page: dict) -> tuple[int, int, int]` returning `(seen, new, skipped)`.
  - `poll_feed(feed_url: str, *, fetch=fetch_json, max_pages: int = 20) -> IngestStats`
  - `run_ingest(feed_url: str, *, fetch=fetch_json, max_pages: int = 20, source: str = "feed") -> IngestRun`
- `ingest_page` stores only `site == "reddit"` items; everything else increments `skipped`. It creates or updates `Thread`, sets `thread.title` from the first post title, and updates `first_seen`, `last_seen`, `mention_count`.
- `ingest_page` attaches `Thread.subreddit` by lower-case name. If no `Subreddit` row exists, it creates a placeholder with `kind="general"`, `source=""`, `confidence=""`; milestone 2 replaces the placeholder through the classifier.
- `poll_feed` stops when `seen > 0`, when `items` is empty, when `next_url` is falsy, or when `max_pages` is reached.
- `run_ingest` wraps `poll_feed`, creates an `IngestRun` before work, sets counts and `finished_at`, and records `error` without ever including `feed_url`.

- [ ] **Step 1: Write the failing service tests**

Create `tests/watch/test_ingest.py`:

```python
import json
from pathlib import Path

import pytest

from watch.ingest import IngestStats, ingest_page, poll_feed, run_ingest
from watch.models import IngestRun, Mention, Thread

FIXTURES = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIXTURES / name).read_text())


@pytest.mark.django_db
def test_fixture_creates_88_mentions_and_69_threads():
    seen, new, skipped = ingest_page(load("f5bot_page_1.json"))
    assert (seen, new, skipped) == (0, 50, 0)
    seen, new, skipped = ingest_page(load("f5bot_page_2.json"))
    assert (seen, new, skipped) == (0, 38, 0)
    assert Mention.objects.count() == 88
    assert Thread.objects.count() == 69
    la = Thread.objects.get(key="reddit:losangeles/1xzzze7")
    assert la.mention_count == 16
    assert la.subreddit.name == "losangeles"


@pytest.mark.django_db
def test_second_ingest_is_idempotent():
    ingest_page(load("f5bot_page_1.json"))
    ingest_page(load("f5bot_page_2.json"))
    assert ingest_page(load("f5bot_page_1.json")) == (50, 0, 0)
    assert Mention.objects.count() == 88
    assert Thread.objects.count() == 69


@pytest.mark.django_db
def test_poll_feed_follows_next_url_then_stops():
    pages = {
        "fixture:page-1": load("f5bot_page_1.json"),
        "fixture:page-2": load("f5bot_page_2.json"),
    }
    stats = poll_feed("fixture:page-1", fetch=pages.__getitem__)
    assert stats == IngestStats(pages=2, seen=0, new=88)
    again = poll_feed("fixture:page-1", fetch=pages.__getitem__)
    assert again == IngestStats(pages=1, seen=50, new=0)


@pytest.mark.django_db
def test_non_reddit_items_are_skipped():
    page = load("f5bot_page_1.json")
    page["items"][0]["url"] = "https://news.ycombinator.com/item?id=4242"
    seen, new, skipped = ingest_page(page)
    assert (seen, new, skipped) == (0, 49, 1)


@pytest.mark.django_db
def test_run_ingest_records_counts_and_hides_feed_url(settings):
    settings.F5BOT_FEED_URL = "https://f5bot.com/alert-json-feed?a=SECRET&h=SECRET"
    pages = {
        "fixture:page-1": load("f5bot_page_1.json"),
        "fixture:page-2": load("f5bot_page_2.json"),
    }
    run = run_ingest("fixture:page-1", fetch=pages.__getitem__)
    assert run.new == 88
    assert run.finished_at is not None
    assert "SECRET" not in run.source
    assert "SECRET" not in run.error
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/watch/test_ingest.py -q --no-cov`
Expected: FAIL because `watch.ingest` does not exist.

- [ ] **Step 3: Implement the service**

Use `transaction.atomic` around each page. For each parsed row, skip if `Mention.objects.filter(pk=row["id"]).exists()`. Use `Thread.objects.get_or_create(key=row["thread_key"], defaults=...)`. Update `last_seen` and `mention_count` with `F()` expressions or Python increments inside the transaction. Create `Mention` with `raw_json=item`, `highlights=json.loads(row["highlights"])`, `subreddit_name=row["subreddit"]`, and `published_at=datetime.fromisoformat(row["published_at"])` when present (the parser returns an aware UTC ISO string). Create or attach the placeholder `Subreddit` described in the Interfaces block. Milestone 2 replaces its placeholder values through the classifier.

For `run_ingest`, set `source="feed"` (or `"file"` for the command) and never include the URL. On exception, set `error=f"{type(exc).__name__}: {exc}"`, ensuring the URL is not in the message; if the exception text contains the URL, replace it with `"[feed url redacted]"`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/watch/test_ingest.py -q --no-cov`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add watch/ingest.py tests/watch/test_ingest.py
git commit -m "feat(watch): ingest f5bot pages into threads and mentions"
```

---

### Task 5: `ingest_f5bot` command and Redis lock

**Files:**
- Create: `watch/management/__init__.py`, `watch/management/commands/__init__.py`, `watch/management/commands/ingest_f5bot.py`
- Modify: `watch/ingest.py`
- Test: `tests/watch/test_ingest_command.py`

**Interfaces:**
- Consumes: `run_ingest` from Task 4.
- Produces: `ingest_f5bot --max-pages N`, `--file PATH` (repeatable), `--feed-url URL`, and `--no-lock` for tests. `--file` ingests one saved page per file and records one `IngestRun` per file with `source="file:<name>"`.
- Produces: `acquire_ingest_lock() -> context manager` using `django_rq.get_connection("default").lock("watch:ingest", timeout=settings.WATCH_INGEST_LOCK_SECONDS, blocking=False)`. If the lock is held, the command exits with a warning and does not create an `IngestRun`.

- [ ] **Step 1: Write the failing command tests**

```python
import json
from pathlib import Path

import pytest
from django.core.management import call_command

from watch.models import IngestRun, Mention

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.django_db
def test_command_ingests_file_without_lock():
    call_command(
        "ingest_f5bot", "--file", str(FIXTURES / "f5bot_page_1.json"), "--no-lock"
    )
    assert Mention.objects.count() == 50
    assert IngestRun.objects.get().source.startswith("file:")


@pytest.mark.django_db
def test_command_skips_when_lock_held(monkeypatch):
    from watch import ingest

    monkeypatch.setattr(ingest, "acquire_ingest_lock", lambda: None)
    call_command("ingest_f5bot", "--file", str(FIXTURES / "f5bot_page_1.json"))
    assert IngestRun.objects.count() == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/watch/test_ingest_command.py -q --no-cov`
Expected: FAIL because the command does not exist.

- [ ] **Step 3: Implement the command and lock**

Use `@contextmanager` for the lock. The command reads `settings.F5BOT_FEED_URL` when `--feed-url` is absent and errors with `CommandError("F5BOT_FEED_URL is not set")` if both are empty. For `--file`, load JSON with `json.loads(Path(path).read_text())` and call `ingest_page`, then create/finish an `IngestRun` around it.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/watch/test_ingest_command.py -q --no-cov`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add watch/management watch/ingest.py tests/watch/test_ingest_command.py
git commit -m "feat(watch): add locked ingest_f5bot command"
```

---

### Task 6: Enrichment stub and RQ task

**Files:**
- Create: `watch/enrichment.py`, `watch/tasks.py`
- Modify: `watch/ingest.py`
- Test: `tests/watch/test_tasks.py`

**Interfaces:**
- Produces: `enrich_thread(thread_id: str) -> dict[str, str | int]` in `watch/enrichment.py`. Milestone 1 implementation is a no-op that returns `{"status": "stubbed", "thread_id": str(thread_id)}`; later milestones replace only this function body.
- Produces: `enqueue_enrichment(thread_id: str) -> str | None` in `watch/ingest.py`, using `django_rq.get_queue("default").enqueue(enrich_thread, str(thread_id), job_timeout=120)`. It logs and returns `None` if enqueueing fails; it never rolls back the ingest.
- `ingest_page` calls `enqueue_enrichment` once per new or changed thread.

- [ ] **Step 1: Write the failing tests**

```python
import pytest

from watch import enrichment, ingest
from watch.models import Thread


@pytest.mark.django_db
def test_enrich_stub_returns_thread_id(thread):
    assert enrichment.enrich_thread(str(thread.id))["status"] == "stubbed"


@pytest.mark.django_db
def test_enqueue_failure_does_not_break_ingest(monkeypatch):
    import django_rq

    monkeypatch.setattr(
        django_rq,
        "get_queue",
        lambda name: (_ for _ in ()).throw(RuntimeError("no redis")),
    )
    assert ingest.enqueue_enrichment("thread-id") is None
```

Use the existing `thread` fixture from `tests/conftest.py` if present; otherwise create one with `ThreadFactory`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/watch/test_tasks.py -q --no-cov`
Expected: FAIL because the modules do not exist.

- [ ] **Step 3: Implement the stub and enqueue helper**

Keep `enrich_thread` free of model imports beyond `Thread` so later milestones can expand it. Use `logger.exception` in `enqueue_enrichment`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/watch/test_tasks.py -q --no-cov`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add watch/enrichment.py watch/tasks.py watch/ingest.py tests/watch/test_tasks.py
git commit -m "feat(watch): enqueue thread enrichment jobs"
```

---

### Task 7: Admin and milestone verification

**Files:**
- Create: `watch/admin.py`
- Create: `docs/operations/civicband-watch-deploy.md` (milestone 1 section; milestone 6 extends it)
- Test: `tests/watch/test_admin.py`

**Interfaces:**
- Produces: admin registrations for `Thread`, `Mention`, `Subreddit`, `Place`, `PlaceAlias`, `ThreadPlace`, `PlaceMention`, `MeetingHit`, `IngestRun`, `PlaceNote`, `MutedCountry`.
- `ThreadAdmin.list_display`: key, title, subreddit, status, mention_count, last_seen.
- `IngestRunAdmin` is read-only and newest first.
- `MentionAdmin` is read-only; do not expose `raw_json` in the changelist.

- [ ] **Step 1: Write the failing admin test**

```python
import pytest
from django.urls import reverse

from tests.factories import IngestRunFactory, MentionFactory


@pytest.mark.django_db
def test_admin_lists_threads_and_ingest_runs(admin_client):
    mention = MentionFactory()
    IngestRunFactory()
    assert admin_client.get(reverse("admin:watch_thread_changelist")).status_code == 200
    assert (
        admin_client.get(reverse("admin:watch_ingestrun_changelist")).status_code == 200
    )
    assert (
        admin_client.get(reverse("admin:watch_mention_changelist")).status_code == 200
    )
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/watch/test_admin.py -q --no-cov`
Expected: FAIL because the admin classes are not registered.

- [ ] **Step 3: Implement the admin**

Use `@admin.register`, `list_select_related`, `search_fields`, and `readonly_fields` as appropriate. Do not add custom actions in this milestone.

Create `docs/operations/civicband-watch-deploy.md` with the milestone 1 section: migrate, set `F5BOT_FEED_URL`, run `manage.py ingest_f5bot` manually, and add the hourly cron entry. State that the crontab itself lives in `public-works`, not this repo.

- [ ] **Step 4: Run the full watch test suite and the repo checks**

Run:

```bash
uv run pytest tests/watch -q --no-cov
uv run --group dev ruff check watch tests/watch
uv run --group dev mypy watch
```

Expected: all pass. Then run `uv run pytest -q` to confirm the coverage floor is still met.

- [ ] **Step 5: Commit**

```bash
git add watch/admin.py tests/watch/test_admin.py docs/operations/civicband-watch-deploy.md
git commit -m "feat(watch): register watch admin"
```

---

## Milestone 1 Acceptance

- Ingesting the two anonymized fixtures creates 88 mentions and 69 threads.
- A second ingest creates zero mentions and zero threads.
- `poll_feed` follows `next_url` once and stops on the first already-seen page.
- Non-Reddit items are counted as `skipped`.
- Every run is recorded in `IngestRun`, including failures, with no feed secret in the record.
- The parser tests pass.
- `uv run pytest -q`, ruff, and mypy pass.
