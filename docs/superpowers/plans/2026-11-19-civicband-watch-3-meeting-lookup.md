# CivicBand Watch — Milestone 3: CivicBand Meeting Lookup

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn a thread’s subject places into cached CivicBand meeting hits using civic-observer’s existing BM25 backend, with no HTTP round-trip and no duplicates.

**Architecture:** `watch/lookup.py` builds a local query from the thread title and snippets, calls `searches.search_backends.get_search_backend().search_with_cache()` for each covered subject place, maps the dict results into `MeetingHit` rows, and replaces only the hits for that thread/place. `enrich_thread` calls it after classification/extraction exist; in this milestone, tests create `ThreadPlace` rows directly.

**Tech Stack:** Django 5.2, ParadeDB BM25 through `searches.search_backends`, pytest, factory-boy.

**Spec:** `docs/superpowers/specs/2026-11-19-civicband-watch-design.md`

## Global Constraints

- Use the existing backend. Never write raw SQL against `meetings_meetingpage` or alter the BM25 index.
- Import `searches.search_backends` as a module and call `searches.search_backends.get_search_backend()` at runtime. The test suite patches that module attribute; a direct `from ... import get_search_backend` would bypass the mock.
- Search only places with a linked `Muni`. A place without a `Muni` produces no hits.
- Limit to 5 hits per subject place. Cache the query used to produce each hit.
- Search the last 12 months first; fall back to all dates only when the recent search returns nothing.
- No LLM. Query terms are built locally.
- Never touch the `meetings` or `municipalities` tables in a migration.

## Review Focus

- Re-running the same lookup must not add duplicate `MeetingHit` rows.
- A place with no `Muni` must produce zero hits and no search call.
- A search that returns no recent results must retry without `date_from` exactly once.
- A result whose page no longer exists must not break rendering; citation fields are denormalized onto the hit.
- A future-dated agenda must be marked so the triage view can offer a snooze.

---

### Task 1: Query builder

**Files:**
- Create: `watch/lookup.py`
- Test: `tests/watch/test_query_builder.py`

**Interfaces:**
- Produces:
  - `STOPWORDS: frozenset[str]` including common English words, `reddit`, `thread`, `comment`, `update`, `news`, `city`, `county`, `council`, `board`, `meeting`, `commission`.
  - `build_query(thread: Thread, *, place: Place | None = None) -> str`
  - `capitalized_phrases(text: str) -> list[str]`
- Rules:
  - If `place` is supplied and a `ThreadPlace(thread=thread, place=place)` has a non-blank `query_override`, return that string unchanged.
  - Start with `thread.title`.
  - Add capitalized phrases of 1–4 words from each mention’s `snippet_text`, preserving order.
  - Remove the f5bot keywords, the place name, `PlaceAlias` keys, stopwords, and tokens shorter than 3 characters.
  - Deduplicate case-insensitively and cap at 25 terms.
  - If the result is empty, use `thread.title`; if that is empty, use `place.name`.
  - Return a single space-joined string.

- [ ] **Step 1: Write the failing tests**

```python
import pytest

from tests.factories import MentionFactory, PlaceFactory, ThreadFactory
from watch.lookup import build_query, capitalized_phrases


def test_capitalized_phrases_finds_named_entities():
    assert capitalized_phrases(
        "The Beverly Hills Planning Commission meets Tuesday."
    ) == [
        "Beverly Hills Planning Commission",
        "Hills Planning Commission",
        "Planning Commission",
        "Beverly Hills",
        "Tuesday",
    ]


@pytest.mark.django_db
def test_query_excludes_keyword_and_place():
    place = PlaceFactory(name="Chico", state="CA")
    thread = ThreadFactory(title="Chico council delays budget vote")
    MentionFactory(
        thread=thread,
        keyword="city council",
        snippet_text="The Chico City Council delayed the budget vote.",
    )
    query = build_query(thread, place=place)
    assert "Chico" not in query
    assert "city council" not in query
    assert "budget" in query


@pytest.mark.django_db
def test_query_falls_back_to_title():
    thread = ThreadFactory(title="Only a title")
    assert build_query(thread) == "Only a title"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/watch/test_query_builder.py -q --no-cov`
Expected: FAIL because `watch.lookup` does not exist.

- [ ] **Step 3: Implement the query builder**

Use a capitalized run of at most four words and generate all contiguous subphrases up to four words, so “Beverly Hills Planning Commission” also yields “Beverly Hills”, “Planning Commission”, etc. Do not use an LLM or an external NLP library.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/watch/test_query_builder.py -q --no-cov`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add watch/lookup.py tests/watch/test_query_builder.py
git commit -m "feat(watch): build local meeting search queries"
```

---

### Task 2: Search wrapper and result mapping

**Files:**
- Modify: `watch/lookup.py`
- Test: `tests/watch/test_lookup_search.py`

**Interfaces:**
- Produces:
  - `search_place(place: Place, query: str, *, limit: int = 5) -> list[dict]`
  - `_search(query: str, muni: Muni, *, date_from: date | None, limit: int) -> tuple[list[dict], int]`
- `_search` calls:

```python
from searches import search_backends

backend = search_backends.get_search_backend()
return backend.search_with_cache(
    query_text=query,
    municipalities=[muni],
    date_from=date_from,
    limit=limit,
)
```

- `search_place` first calls `_search` with `date_from=timezone.localdate() - timedelta(days=365)`, then retries with `date_from=None` only if the first result list is empty.

- [ ] **Step 1: Write the failing tests**

Use the existing `mock_search_backend` autouse fixture and patch `search_with_cache` to record calls:

```python
@pytest.mark.django_db
def test_search_place_falls_back_to_all_dates(monkeypatch):
    place = PlaceWithMuniFactory()
    calls = []

    def fake_search(**kwargs):
        calls.append(kwargs)
        return ([], 0) if kwargs["date_from"] else ([{"id": "p1"}], 1)

    monkeypatch.setattr(
        "searches.search_backends.get_search_backend",
        lambda: SimpleNamespace(search_with_cache=fake_search),
    )
    assert search_place(place, "budget") == [{"id": "p1"}]
    assert len(calls) == 2
    assert calls[0]["date_from"] is not None
    assert calls[1]["date_from"] is None


@pytest.mark.django_db
def test_search_place_without_muni_returns_empty(monkeypatch):
    place = PlaceFactory(muni=None)
    monkeypatch.setattr(
        "searches.search_backends.get_search_backend",
        lambda: pytest.fail("must not search"),
    )
    assert search_place(place, "budget") == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/watch/test_lookup_search.py -q --no-cov`
Expected: FAIL because `search_place` does not exist.

- [ ] **Step 3: Implement the wrapper**

Do not catch backend exceptions broadly. Let RQ record a failed job; the enrichment task logs and returns a failure status.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/watch/test_lookup_search.py -q --no-cov`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add watch/lookup.py tests/watch/test_lookup_search.py
git commit -m "feat(watch): call the existing bm25 backend for places"
```

---

### Task 3: Persist and refresh `MeetingHit` rows

**Files:**
- Modify: `watch/lookup.py`
- Test: `tests/watch/test_meeting_hits.py`

**Interfaces:**
- Produces:
  - `query_hash(query: str) -> str` returning `hashlib.sha256(query.encode()).hexdigest()`.
  - `result_to_hit(thread, place, query, result) -> MeetingHit` with citation fields from the search dict:
    - `page_id=result["id"]`
    - `rank=result.get("rank")`
    - `snippet=result.get("snippet") or ""`
    - `meeting_name=result.get("meeting_name") or ""`
    - `meeting_date=result.get("meeting_date")`
    - `document_type=result.get("document_type") or ""`
    - `civic_band_url=f"https://{result['subdomain']}.civic.band/meetings/{result['civic_band_table_name']}/{result['id']}"`
    - `thumbnail_url` built from `result["subdomain"]` and `result["page_image"]` when present, using `settings.CDN_URL`
  - `refresh_thread_hits(thread: Thread) -> int` for all subject places with a `Muni`.
  - `meeting_hits_for(thread: Thread) -> QuerySet[MeetingHit]` prefetching `place`.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.django_db
def test_refresh_replaces_hits_without_duplicates(monkeypatch):
    thread = ThreadFactory()
    place = PlaceWithMuniFactory()
    ThreadPlace.objects.create(
        thread=thread,
        place=place,
        role="subject",
        confidence="high",
        source="subreddit",
    )
    results = [
        {
            "id": f"page-{n}",
            "rank": 1.0 - n / 10,
            "snippet": "<mark>budget</mark>",
            "meeting_name": "CityCouncil",
            "meeting_date": date(2024, 1, 15),
            "document_type": "agenda",
            "subdomain": "chico",
            "civic_band_table_name": "agendas",
            "page_image": "/_agendas/CityCouncil/2024-01-15/1.png",
        }
        for n in range(3)
    ]
    monkeypatch.setattr(
        "searches.search_backends.get_search_backend",
        lambda: SimpleNamespace(search_with_cache=lambda **kwargs: (results, 3)),
    )
    assert refresh_thread_hits(thread) == 3
    assert refresh_thread_hits(thread) == 3
    assert MeetingHit.objects.filter(thread=thread).count() == 3


@pytest.mark.django_db
def test_uncovered_place_is_not_searched(monkeypatch):
    thread = ThreadFactory()
    ThreadPlace.objects.create(
        thread=thread,
        place=PlaceFactory(muni=None),
        role="subject",
        confidence="high",
        source="subreddit",
    )
    monkeypatch.setattr(
        "searches.search_backends.get_search_backend",
        lambda: pytest.fail("must not search"),
    )
    assert refresh_thread_hits(thread) == 0


@pytest.mark.django_db
def test_future_agenda_is_flagged():
    hit = MeetingHitFactory(meeting_date=timezone.localdate() + timedelta(days=3))
    assert hit.is_future is True
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/watch/test_meeting_hits.py -q --no-cov`
Expected: FAIL because `refresh_thread_hits` does not exist.

- [ ] **Step 3: Implement persistence**

For each covered subject place: build the query, search, delete `MeetingHit` rows for `(thread, place)` inside a transaction, and bulk-create the mapped hits. Add a `MeetingHit.is_future` property to the model; it returns `bool(self.meeting_date and self.meeting_date > timezone.localdate())`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/watch/test_meeting_hits.py -q --no-cov`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add watch/lookup.py watch/models.py watch/migrations tests/watch/test_meeting_hits.py
git commit -m "feat(watch): cache and refresh meeting hits"
```

---

### Task 4: Wire lookup into enrichment

**Files:**
- Modify: `watch/enrichment.py`
- Test: `tests/watch/test_enrichment.py`

**Interfaces:**
- Produces: `enrich_thread(thread_id: str) -> dict[str, int | str]` that:
  - Loads the thread.
  - Calls `refresh_thread_hits(thread)`.
  - Returns `{"status": "ok", "thread_id": str(thread.id), "hits": count}`.
  - Returns `{"status": "not_found", "thread_id": thread_id}` for a missing thread.
- This milestone does not classify or extract; those calls arrive in milestone 5. Keep the function small and idempotent.
- Update the milestone 1 `test_enrich_stub_returns_thread_id` to expect `status="ok"`; the stub is replaced here.

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.django_db
def test_enrich_calls_refresh(monkeypatch):
    thread = ThreadFactory()
    monkeypatch.setattr("watch.enrichment.refresh_thread_hits", lambda t: 4)
    assert enrich_thread(str(thread.id)) == {
        "status": "ok",
        "thread_id": str(thread.id),
        "hits": 4,
    }
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/watch/test_enrichment.py -q --no-cov`
Expected: FAIL because `enrich_thread` is still the stub.

- [ ] **Step 3: Implement the call**

Use `Thread.objects.filter(pk=thread_id).first()`; do not raise.

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/watch/test_enrichment.py -q --no-cov`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add watch/enrichment.py tests/watch/test_enrichment.py
git commit -m "feat(watch): refresh meeting hits during enrichment"
```

---

### Task 5: Operator command to refresh hits

**Files:**
- Create: `watch/management/commands/refresh_watch_hits.py`
- Test: `tests/watch/test_refresh_hits_command.py`

**Interfaces:**
- Command options: `--thread UUID` (repeatable), `--all`, `--limit N`.
- Without `--thread` or `--all`, print a warning and exit.
- `--all` iterates threads with at least one covered subject place, ordered by `-last_seen`, up to `--limit`.

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.django_db
def test_refresh_command_for_one_thread(monkeypatch, capsys):
    thread = ThreadFactory()
    monkeypatch.setattr(
        "watch.management.commands.refresh_watch_hits.refresh_thread_hits", lambda t: 2
    )
    call_command("refresh_watch_hits", "--thread", str(thread.id))
    assert "2 hits" in capsys.readouterr().out
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/watch/test_refresh_hits_command.py -q --no-cov`
Expected: FAIL because the command does not exist.

- [ ] **Step 3: Implement the command**

Use `Thread.objects.filter(pk=thread_id).first()` and print a clear message for missing ids.

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/watch/test_refresh_hits_command.py -q --no-cov`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add watch/management/commands/refresh_watch_hits.py tests/watch/test_refresh_hits_command.py
git commit -m "feat(watch): add refresh_watch_hits command"
```

---

### Task 6: Milestone verification

Run:

```bash
uv run pytest tests/watch -q --no-cov
uv run --group dev ruff check watch tests/watch
uv run --group dev mypy watch
```

Expected: all pass.

## Milestone 3 Acceptance

- A thread whose subject place links to a `Muni` gets up to 5 cached hits per place.
- Re-running with the same query adds no duplicates.
- A place without a `Muni` produces zero hits and zero search calls.
- Recent-first search falls back to all dates exactly once.
- Future-dated agenda hits expose `is_future=True`.
- Tests exercise the backend the same way `tests/searches/` does, through the patched module attribute.
- `uv run pytest -q`, ruff, and mypy pass.
