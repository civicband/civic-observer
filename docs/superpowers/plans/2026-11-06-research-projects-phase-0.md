# Research Projects — Phase 0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract one shared `QuerySpec`/`build_where` query layer used by interactive search, alerting and (later) Research sets, add a `modified_after` cutoff, and measure the query/insert cost so the Research set ceiling rests on numbers.

**Architecture:** `QuerySpec` is an immutable value object in `searches/queryspec.py`. `build_where()` turns it into SQL + params using an optional table alias. `PgSearchBackend` and the alerting services call it instead of hand-building predicates. `modified_after` filters on `MeetingPage.modified` inside the BM25 query (never as a Python post-filter). A management command records `EXPLAIN (ANALYZE, BUFFERS)` timings for the canonical cases.

**Tech Stack:** Django 5.2, PostgreSQL 17, ParadeDB pg_search, django-rq, pytest, factory_boy.

**Spec:** `docs/superpowers/specs/2026-11-06-research-projects-design.md`

## Global Constraints

- **Vocabulary:** models/API/exports say `Code`, `Coding`, "codebook"; the UI says "Tags", "Tag guide". Never rename either side to match the other. (Not exercised in Phase 0; preserved for later tasks.)
- **Page references:** every reference to a meeting page uses `db_constraint=False, on_delete=DO_NOTHING`.
- **Sets are snapshots.** New matches arrive only through an explicit, logged action.
- **No new runtime dependencies.** Use the existing Django/ParadeDB/RQ stack.
- **One query layer:** interactive search, alerting, set materialization, ignore rules and lens sub-searches all call `build_where`. A set must contain exactly what the search page showed.
- **`modified_after` uses `MeetingPage.modified`, not `created`.** A re-OCR'd page keeps its id and may only now match.
- **Postgres only.** Tests run against PostgreSQL at `localhost:5433`; ParadeDB operators are unavailable there, so backend SQL tests mock the cursor and the shared `tests/conftest.py` backend mock must support every new filter.
- **No silently truncated sets.** A capped result must be reported, never silently returned as if complete.

## Review Focus

These are the failure modes Phase 0 can plausibly introduce. Each line's test lives in the task named in parentheses.

1. An empty query with no filters must keep the current `TRUE` recency listing, not rank the whole corpus or error (Task 2).
2. `document_type` values `""`, `None` and `"all"` must all mean "no filter"; an empty municipality list must mean "no filter" (Task 2).
3. `modified_after` must reach SQL and the cache key, or alerting reuses stale results (Tasks 2 and 3).
4. Fetching more than one page of ids must have a unique ORDER BY tiebreaker, or pages duplicate/skip at page boundaries (Task 2).
5. Reaching the safety ceiling must log a warning rather than silently under-report (Task 3).

---

### Task 1: `QuerySpec` value object

**Files:**
- Create: `searches/queryspec.py`
- Test: `tests/searches/test_queryspec.py`

**Interfaces:**
- Consumes: `Search` model fields (`search_term`, `meeting_name_query`, `municipalities`, `states`, `date_from`, `date_to`, `document_type`).
- Produces:
  - `QuerySpec(text="", meeting_name="", municipality_ids=(), states=(), date_from=None, date_to=None, document_type="all", modified_after=None)` — frozen dataclass.
  - `QuerySpec.canonical() -> dict`
  - `QuerySpec.canonical_json() -> str`
  - `QuerySpec.query_hash() -> str`
  - `QuerySpec.from_search(search, *, modified_after=None) -> QuerySpec`
  - `QuerySpec.with_modified_after(cutoff) -> QuerySpec`

- [ ] **Step 1: Write the failing tests**

Create `tests/searches/test_queryspec.py`:

```python
import uuid
from datetime import date, datetime, timezone

import pytest

from searches.queryspec import QuerySpec
from tests.factories import MuniFactory, SearchFactory


class TestCanonical:
    def test_normalizes_whitespace_order_and_empty_document_type(self):
        spec = QuerySpec(
            text="  housing ",
            meeting_name=" council ",
            municipality_ids=(uuid.UUID(int=2), uuid.UUID(int=1)),
            states=("OR", "CA", "CA"),
            document_type="",
        )

        canonical = spec.canonical()

        assert canonical["text"] == "housing"
        assert canonical["meeting_name"] == "council"
        assert canonical["municipality_ids"] == [
            str(uuid.UUID(int=1)),
            str(uuid.UUID(int=2)),
        ]
        assert canonical["states"] == ["CA", "OR"]
        assert canonical["document_type"] == "all"
        assert canonical["date_from"] is None
        assert canonical["modified_after"] is None

    def test_dates_use_isoformat(self):
        spec = QuerySpec(
            date_from=date(2024, 1, 2),
            date_to=date(2024, 3, 4),
            modified_after=datetime(2024, 5, 6, 7, 8, 9, tzinfo=timezone.utc),
        )

        canonical = spec.canonical()

        assert canonical["date_from"] == "2024-01-02"
        assert canonical["date_to"] == "2024-03-04"
        assert canonical["modified_after"] == "2024-05-06T07:08:09+00:00"

    def test_hash_is_stable_across_input_order(self):
        a = QuerySpec(
            text="housing",
            municipality_ids=(uuid.UUID(int=1), uuid.UUID(int=2)),
            states=("CA", "OR"),
        )
        b = QuerySpec(
            text="housing",
            municipality_ids=(uuid.UUID(int=2), uuid.UUID(int=1)),
            states=("OR", "CA"),
        )

        assert a.canonical_json() == b.canonical_json()
        assert a.query_hash() == b.query_hash()

    def test_hash_changes_when_modified_after_changes(self):
        first = QuerySpec(
            text="housing",
            modified_after=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
        second = QuerySpec(
            text="housing",
            modified_after=datetime(2024, 1, 2, tzinfo=timezone.utc),
        )

        assert first.query_hash() != second.query_hash()


@pytest.mark.django_db
class TestFromSearch:
    def test_copies_all_search_fields(self):
        ca = MuniFactory(state="CA")
        or_ = MuniFactory(state="OR")
        search = SearchFactory(
            search_term="  housing ",
            meeting_name_query=" council ",
            states=["CA", "OR"],
            date_from=date(2024, 1, 1),
            date_to=date(2024, 12, 31),
            document_type="agenda",
        )
        search.municipalities.add(or_, ca)

        spec = QuerySpec.from_search(search, modified_after=datetime(2024, 6, 1))

        assert spec.text == "housing"
        assert spec.meeting_name == "council"
        assert set(spec.municipality_ids) == {ca.id, or_.id}
        assert set(spec.states) == {"CA", "OR"}
        assert spec.date_from == date(2024, 1, 1)
        assert spec.date_to == date(2024, 12, 31)
        assert spec.document_type == "agenda"
        assert spec.modified_after == datetime(2024, 6, 1)

    def test_with_modified_after_returns_new_spec(self):
        spec = QuerySpec(text="housing")

        updated = spec.with_modified_after(datetime(2024, 6, 1))

        assert spec.modified_after is None
        assert updated.modified_after == datetime(2024, 6, 1)
        assert updated.text == "housing"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --group test pytest tests/searches/test_queryspec.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'searches.queryspec'`.

- [ ] **Step 3: Write the implementation**

Create `searches/queryspec.py`:

```python
"""Canonical, immutable description of a meeting-page search.

Every query path that claims to show "the same results" — the interactive
search, alerting, and Research set materialization — builds one of these and
then calls :func:`build_where`. The frozen JSON form is also what Research
stores on a set and hashes into ``query_hash``.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, replace
from datetime import date, datetime


def _clean(value: str | None) -> str:
    return value.strip() if value else ""


@dataclass(frozen=True)
class QuerySpec:
    text: str = ""
    meeting_name: str = ""
    municipality_ids: tuple[uuid.UUID, ...] = ()
    states: tuple[str, ...] = ()
    date_from: date | None = None
    date_to: date | None = None
    document_type: str = "all"
    modified_after: datetime | None = None

    def canonical(self) -> dict:
        """Return a normalized dict suitable for storage and hashing.

        Ordering of municipalities and states is removed so the same logical
        query always produces the same hash.
        """
        return {
            "text": _clean(self.text),
            "meeting_name": _clean(self.meeting_name),
            "municipality_ids": sorted(
                str(muni_id) for muni_id in self.municipality_ids
            ),
            "states": sorted({state for state in self.states if state}),
            "date_from": self.date_from.isoformat() if self.date_from else None,
            "date_to": self.date_to.isoformat() if self.date_to else None,
            "document_type": self.document_type or "all",
            "modified_after": (
                self.modified_after.isoformat() if self.modified_after else None
            ),
        }

    def canonical_json(self) -> str:
        return json.dumps(self.canonical(), sort_keys=True, separators=(",", ":"))

    def query_hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode()).hexdigest()

    @classmethod
    def from_search(cls, search, *, modified_after: datetime | None = None):
        return cls(
            text=_clean(search.search_term),
            meeting_name=_clean(search.meeting_name_query),
            municipality_ids=tuple(
                sorted(search.municipalities.values_list("id", flat=True), key=str)
            ),
            states=tuple(search.states or ()),
            date_from=search.date_from,
            date_to=search.date_to,
            document_type=search.document_type or "all",
            modified_after=modified_after,
        )

    def with_modified_after(self, cutoff: datetime | None) -> "QuerySpec":
        return replace(self, modified_after=cutoff)


def _column(alias: str, name: str) -> str:
    return f"{alias}.{name}" if alias else name
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group test pytest tests/searches/test_queryspec.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add searches/queryspec.py tests/searches/test_queryspec.py
git commit -m "feat: add canonical QuerySpec for shared search predicates"
```

---

### Task 2: `build_where` + backend/cache plumbing

**Files:**
- Modify: `searches/queryspec.py` (add `build_where`)
- Modify: `searches/search_backends.py`
- Modify: `searches/cache.py`
- Test: `tests/searches/test_queryspec.py`
- Test: `tests/searches/test_pg_search_backend.py`
- Test: `tests/searches/test_cache.py` (create if absent)

**Interfaces:**
- Consumes: `QuerySpec` from Task 1.
- Produces:
  - `build_where(spec: QuerySpec, alias: str = "mp") -> tuple[str, list]`
  - `SearchBackend.search(..., modified_after=None)` and `search_with_cache(..., modified_after=None)` — keyword added to both abstract and concrete signatures.
  - `searches.cache._make_search_cache_key(..., modified_after: str | None = None)`; `get_cached_search_results(..., modified_after=None)`; `set_cached_search_results(..., modified_after=None)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/searches/test_queryspec.py`:

```python
from searches.queryspec import build_where


class TestBuildWhere:
    def test_full_spec(self):
        muni_id = uuid.UUID(int=7)
        spec = QuerySpec(
            text=" housing ",
            meeting_name=" council ",
            municipality_ids=(muni_id,),
            states=("CA",),
            date_from=date(2024, 1, 1),
            date_to=date(2024, 12, 31),
            document_type="agenda",
            modified_after=datetime(2024, 6, 1),
        )

        where, params = build_where(spec)

        assert where == (
            "mp.id @@@ paradedb.parse(%s)"
            " AND mp.id @@@ paradedb.parse(%s)"
            " AND mp.municipality_id = ANY(%s)"
            " AND mp.state = ANY(%s)"
            " AND mp.meeting_date >= %s"
            " AND mp.meeting_date <= %s"
            " AND mp.document_type = %s"
            " AND mp.modified >= %s"
        )
        assert params == [
            "text:(housing)",
            "meeting_name:(council)",
            [muni_id],
            ["CA"],
            date(2024, 1, 1),
            date(2024, 12, 31),
            "agenda",
            datetime(2024, 6, 1),
        ]

    def test_empty_spec_is_true(self):
        assert build_where(QuerySpec()) == ("TRUE", [])

    def test_empty_municipalities_do_not_filter(self):
        where, params = build_where(QuerySpec(municipality_ids=()))
        assert where == "TRUE"
        assert params == []

    @pytest.mark.parametrize("document_type", ["", "all", None])
    def test_document_type_all_does_not_filter(self, document_type):
        where, params = build_where(QuerySpec(document_type=document_type))
        assert where == "TRUE"
        assert params == []

    def test_alias_can_be_removed(self):
        where, _ = build_where(QuerySpec(text="housing"), alias="")
        assert where == "id @@@ paradedb.parse(%s)"
```

Append to `tests/searches/test_pg_search_backend.py`:

```python
from datetime import datetime, timezone


class TestModifiedAfter:
    @patch("searches.search_backends.connection")
    def test_search_filters_on_modified(self, mock_conn):
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_cursor.fetchall.return_value = []

        cutoff = datetime(2024, 6, 1, tzinfo=timezone.utc)

        backend = PgSearchBackend()
        backend.search(query_text="housing", modified_after=cutoff)

        sql = mock_cursor.execute.call_args[0][0]
        params = mock_cursor.execute.call_args[0][1]

        assert "mp.modified >= %s" in sql
        assert cutoff in params

    @patch("searches.search_backends.connection")
    def test_order_has_unique_id_tiebreaker(self, mock_conn):
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_cursor.fetchall.return_value = []

        PgSearchBackend().search(query_text="housing")

        sql = mock_cursor.execute.call_args[0][0]
        assert "ORDER BY paradedb.score(id) DESC, meeting_date DESC, id" in sql
```

Append to `tests/searches/test_cache.py` (create with the header below if the file does not exist):

```python
from searches.cache import _make_search_cache_key


class TestModifiedAfterCacheKey:
    def test_modified_after_changes_cache_key(self):
        base = dict(
            search_term="housing",
            municipalities=[],
            states=[],
            date_from=None,
            date_to=None,
            document_type="all",
            meeting_name_query="",
            limit=100,
            offset=0,
        )

        without = _make_search_cache_key(**base, modified_after=None)
        with_cutoff = _make_search_cache_key(
            **base, modified_after="2024-06-01T00:00:00+00:00"
        )

        assert without != with_cutoff
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --group test pytest tests/searches/test_queryspec.py tests/searches/test_pg_search_backend.py tests/searches/test_cache.py -v`
Expected: FAIL (`build_where` missing, `modified_after` unexpected keyword, cache key assertion fails).

- [ ] **Step 3: Add `build_where`**

Append to `searches/queryspec.py`:

```python
def build_where(spec: QuerySpec, alias: str = "mp") -> tuple[str, list]:
    """Render ``spec`` as a SQL WHERE clause and positional parameters.

    ``alias`` prefixes every column so callers can join or subquery the page
    table. Pass ``alias=""`` for an unqualified single-table query.
    """
    where: list[str] = []
    params: list = []

    if spec.text:
        where.append(f"{_column(alias, 'id')} @@@ paradedb.parse(%s)")
        params.append(f"text:({_clean(spec.text)})")

    if spec.meeting_name:
        where.append(f"{_column(alias, 'id')} @@@ paradedb.parse(%s)")
        params.append(f"meeting_name:({_clean(spec.meeting_name)})")

    if spec.municipality_ids:
        where.append(f"{_column(alias, 'municipality_id')} = ANY(%s)")
        params.append(list(spec.municipality_ids))

    if spec.states:
        where.append(f"{_column(alias, 'state')} = ANY(%s)")
        params.append(list(spec.states))

    if spec.date_from:
        where.append(f"{_column(alias, 'meeting_date')} >= %s")
        params.append(spec.date_from)

    if spec.date_to:
        where.append(f"{_column(alias, 'meeting_date')} <= %s")
        params.append(spec.date_to)

    if spec.document_type and spec.document_type != "all":
        where.append(f"{_column(alias, 'document_type')} = %s")
        params.append(spec.document_type)

    if spec.modified_after:
        where.append(f"{_column(alias, 'modified')} >= %s")
        params.append(spec.modified_after)

    if not where:
        # No query and no filters: a plain recency listing, never a BM25 scan
        # over the whole corpus.
        where.append("TRUE")

    return " AND ".join(where), params
```

- [ ] **Step 4: Refactor `PgSearchBackend.search` and `_count`**

In `searches/search_backends.py`:

1. Import the new module:

```python
from .queryspec import QuerySpec, build_where
```

2. Add `modified_after=None` to the abstract `search` signature and to `search_with_cache` and `PgSearchBackend.search`; thread it through `search_with_cache` (including cache calls).

3. Replace the hand-built `where`/`params` block in `PgSearchBackend.search` with:

```python
muni_ids = (
    list(municipalities.values_list("id", flat=True))
    if municipalities is not None and hasattr(municipalities, "values_list")
    else [m.id if hasattr(m, "id") else m for m in (municipalities or [])]
)
spec = QuerySpec(
    text=query_text or "",
    meeting_name=meeting_name_query or "",
    municipality_ids=tuple(muni_ids),
    states=tuple(states or ()),
    date_from=date_from,
    date_to=date_to,
    document_type=document_type or "all",
    modified_after=modified_after,
)
where_sql, params = build_where(spec)
```

Keep the existing constants (`HEADLINE_START_TAG`, `COUNT_CAP`, etc.).

4. Change the SQL `FROM` clause and ordering to use the alias and a unique tiebreaker:

```sql
            FROM meetings_meetingpage mp
            WHERE {where_sql}
            {order_sql}
            LIMIT %s OFFSET %s
```

```python
order_sql = (
    "ORDER BY paradedb.score(id) DESC, meeting_date DESC, id"
    if query_text
    else "ORDER BY meeting_date DESC, id"
)
```

5. In `_count`, change `FROM meetings_meetingpage` to `FROM meetings_meetingpage mp` (the WHERE now carries the alias).

The existing test expectations (`"id @@@ paradedb.parse(%s)" in sql`, `"municipality_id = ANY(%s)" in sql`, `"state = ANY(%s)" in sql`) remain substrings of the aliased SQL and should still pass.

- [ ] **Step 5: Add `modified_after` to the cache key**

In `searches/cache.py`:

1. Add `modified_after: str | None = None` to `_make_search_cache_key`, `get_cached_search_results`, and `set_cached_search_results`.
2. Add `"modified_after": modified_after or ""` to the params dict in `_make_search_cache_key`.
3. Pass it through both public functions.
4. In `search_backends.py`, pass `modified_after.isoformat() if modified_after else None` to the cache functions and to `self.search(...)`.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run --group test pytest tests/searches/test_queryspec.py tests/searches/test_pg_search_backend.py tests/searches/test_cache.py -v`
Expected: PASS.

- [ ] **Step 7: Run the whole searches suite**

Run: `uv run --group test pytest tests/searches -v`
Expected: PASS (fix any call sites that still construct `QuerySpec`-era kwargs incorrectly).

- [ ] **Step 8: Commit**

```bash
git add searches/queryspec.py searches/search_backends.py searches/cache.py tests/searches/test_queryspec.py tests/searches/test_pg_search_backend.py tests/searches/test_cache.py
git commit -m "feat: build search predicates from QuerySpec and add modified_after"
```

---

### Task 3: Alerting uses the cutoff inside the query

**Files:**
- Modify: `searches/services.py`
- Modify: `tests/conftest.py` (mock backend)
- Modify: `config/settings/base.py` (add `SEARCH_MAX_RESULT_IDS`)
- Test: `tests/searches/test_services.py`
- Test: `tests/searches/test_notification_tasks.py`

**Interfaces:**
- Consumes: `QuerySpec`, backend `modified_after` from Task 2.
- Produces:
  - `search_result_ids(search, limit=10000, modified_after=None) -> list[str]` — when `limit is None`, paginate up to `settings.SEARCH_MAX_RESULT_IDS`.
  - `search_new_pages(search, cutoff) -> tuple[QuerySet[MeetingPage], int]` — cutoff is passed to SQL as `modified_after`, no Python `created` filter.
  - `get_new_pages(search, since=None)` delegates to `search_new_pages`.
  - `execute_search(search)` keeps the 10,000-id interactive/admin cap.

- [ ] **Step 1: Update the test backend mock**

In `tests/conftest.py`, in `search_side_effect`, read and apply the new filter:

```python
modified_after = kwargs.get("modified_after")
...
if modified_after:
    qs = qs.filter(modified__gte=modified_after)
```

- [ ] **Step 2: Write the failing tests**

In `tests/searches/test_services.py`, update `test_execute_search_passes_correct_params_to_backend` to also assert `call_kwargs["modified_after"] is None`, keep `limit == 10000`, and replace `test_execute_search_logs_warning_at_cap` with the new tests below.

Add to `TestExecuteSearch`:

```text
    @patch("searches.search_backends.get_search_backend")
    def test_search_result_ids_paginates_when_uncapped(
        self, mock_get_backend, settings
    ):
        from searches.services import search_result_ids

        settings.SEARCH_RESULT_PAGE_SIZE = 3
        mock_backend = mock_get_backend.return_value
        mock_backend.search.side_effect = [
            ([{"id": f"page-{i}"} for i in range(3)], 3),
            ([{"id": "page-3"}], 1),
        ]

        search = SearchFactory(search_term="housing")

        ids = search_result_ids(search, limit=None)

        assert ids == ["page-0", "page-1", "page-2", "page-3"]
        assert [call.kwargs["offset"] for call in mock_backend.search.call_args_list] == [
            0,
            3,
        ]

    @patch("searches.search_backends.get_search_backend")
    def test_search_result_ids_logs_warning_at_safety_ceiling(
        self, mock_get_backend, caplog, settings
    ):
        import logging

        from searches.services import search_result_ids

        settings.SEARCH_MAX_RESULT_IDS = 2
        mock_backend = mock_get_backend.return_value
        mock_backend.search.return_value = ([{"id": "a"}, {"id": "b"}], 2)

        search = SearchFactory(search_term="housing")

        with caplog.at_level(logging.WARNING):
            search_result_ids(search, limit=None)

        assert "result ceiling" in caplog.text


@pytest.mark.django_db
class TestSearchNewPages:
    @patch("searches.search_backends.get_search_backend")
    def test_cutoff_is_pushed_into_backend(self, mock_get_backend):
        from django.utils import timezone

        from searches.services import search_new_pages

        muni = MuniFactory()
        doc = MeetingDocumentFactory(municipality=muni)
        page = MeetingPageFactory(document=doc, text="housing")
        cutoff = timezone.now()

        mock_backend = mock_get_backend.return_value
        mock_backend.search.return_value = ([{"id": page.id}], 1)

        search = SearchFactory(search_term="housing")
        search.municipalities.add(muni)

        new_pages, total = search_new_pages(search, cutoff)

        assert list(new_pages) == [page]
        assert total == 1
        assert mock_backend.search.call_args.kwargs["modified_after"] == cutoff

    @patch("searches.search_backends.get_search_backend")
    def test_a_page_modified_after_cutoff_counts_as_new(self, mock_get_backend):
        from datetime import timedelta

        from django.utils import timezone

        from searches.services import search_new_pages

        muni = MuniFactory()
        doc = MeetingDocumentFactory(municipality=muni)
        page = MeetingPageFactory(document=doc, text="housing")

        cutoff = timezone.now() - timedelta(days=1)
        MeetingPage.objects.filter(pk=page.pk).update(modified=timezone.now())

        mock_backend = mock_get_backend.return_value
        mock_backend.search.return_value = ([{"id": page.id}], 1)

        search = SearchFactory(search_term="housing")
        search.municipalities.add(muni)

        new_pages, _ = search_new_pages(search, cutoff)

        assert list(new_pages) == [page]
```

**Also rewrite the existing `TestGetNewPages` and `TestSearchServiceIntegration` tests in the same file.** They currently patch `get_search_backend` with a fixed return value and rely on the old Python `created` post-filter, which no longer exists. Remove their explicit `@patch("searches.search_backends.get_search_backend")` decorators and the `mock_backend` setup, and let the autouse ORM mock from `tests/conftest.py` apply the `modified_after` filter. Keep the same assertions: a page created after the cutoff is new; a page created before it is not; `cutoff=None` returns every match.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run --group test pytest tests/searches/test_services.py -v`
Expected: FAIL (missing `search_new_pages` behavior / missing `SEARCH_MAX_RESULT_IDS`).

- [ ] **Step 4: Implement the service changes**

In `config/settings/base.py`, beside the other search settings:

```python
# Absolute ceiling for an uncapped alert scan. A cutoff usually keeps this far
# below the ceiling; the ceiling only protects memory on pathological backfills.
SEARCH_MAX_RESULT_IDS = env.int("SEARCH_MAX_RESULT_IDS", 200_000)
# How many ids one backend round-trip fetches while paging an uncapped scan.
SEARCH_RESULT_PAGE_SIZE = env.int("SEARCH_RESULT_PAGE_SIZE", 10_000)
```

In `searches/services.py`:

```python
from django.conf import settings

from meetings.models import MeetingPage

from .queryspec import QuerySpec
```

Replace `search_result_ids` with:

```python
def search_result_ids(search, limit=10000, modified_after=None):
    """Run a backend search and return matching page ids.

    ``limit=None`` means "no 10k cap": page through the backend until it is
    exhausted, with ``settings.SEARCH_MAX_RESULT_IDS`` as the memory ceiling.
    The cutoff, when given, is applied inside the BM25 query on
    ``MeetingPage.modified``, so a re-OCR'd page can count as new.
    """
    from .search_backends import get_search_backend

    backend = get_search_backend()
    spec = QuerySpec.from_search(search, modified_after=modified_after)

    if limit is None:
        remaining = settings.SEARCH_MAX_RESULT_IDS
        page_size = settings.SEARCH_RESULT_PAGE_SIZE
    else:
        remaining = limit
        page_size = limit

    ids: list[str] = []
    offset = 0
    while remaining > 0:
        fetch = min(page_size, remaining)
        results, _total = backend.search(
            query_text=spec.text,
            municipalities=list(spec.municipality_ids),
            states=list(spec.states),
            date_from=spec.date_from,
            date_to=spec.date_to,
            document_type=spec.document_type,
            meeting_name_query=spec.meeting_name,
            modified_after=spec.modified_after,
            limit=fetch,
            offset=offset,
        )
        ids.extend(result["id"] for result in results)
        remaining -= len(results)
        offset += len(results)
        if len(results) < fetch:
            break

    if limit is None and remaining == 0:
        logger.warning(
            "Search %s hit the %s result ceiling — new-page notifications may "
            "under-report. Narrow the search or raise SEARCH_MAX_RESULT_IDS.",
            search.pk,
            f"{settings.SEARCH_MAX_RESULT_IDS:,}",
        )
    elif limit is not None and len(ids) >= limit:
        logger.warning(
            "Search %s hit %s result cap.",
            search.pk,
            f"{limit:,}",
        )

    return ids
```

Replace `search_new_pages` with:

```python
def search_new_pages(search, cutoff):
    """Return pages matching ``search`` whose ``modified`` is at/after ``cutoff``.

    The cutoff is pushed into the BM25 query, so there is no 10,000-id cap and
    no second ``created`` filter in Python. ``cutoff=None`` means every match.
    """
    page_ids = search_result_ids(search, limit=None, modified_after=cutoff)
    new_pages = MeetingPage.objects.filter(id__in=page_ids)
    return new_pages, len(page_ids)
```

Replace `get_new_pages` with:

```python
def get_new_pages(search, since=None):
    cutoff = since if since is not None else search.last_checked_for_new_pages
    new_pages, _total = search_new_pages(search, cutoff)
    return new_pages
```

Keep `execute_search` calling `search_result_ids(search)` (the 10,000 default).

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run --group test pytest tests/searches/test_services.py tests/searches/test_notification_tasks.py tests/searches/test_integration.py tests/searches/test_models.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add searches/services.py config/settings/base.py tests/conftest.py tests/searches/test_services.py
git commit -m "feat: push new-page cutoff into the search query"
```

---

### Task 4: Dedicated research queue and settings

**Files:**
- Modify: `config/settings/base.py`
- Modify: `config/settings/development.py`
- Modify: `config/settings/test.py`
- Modify: `config/settings/production.py`
- Modify: `docker-compose.yml`
- Modify: `docker-compose.production.yml`
- Test: `tests/settings/test_research_settings.py` (create)

**Interfaces:**
- Consumes: existing `REDIS_URL` pattern.
- Produces: `settings.RESEARCH_QUEUE`, `settings.RESEARCH_SET_MAX_ITEMS`, `settings.RESEARCH_INLINE_SET_MAX_ITEMS`, `settings.RESEARCH_JOB_TIMEOUT`; a `research` RQ queue in every environment; both worker commands listen to `default` and `research`.

- [ ] **Step 1: Write the failing test**

Create `tests/settings/test_research_settings.py`:

```python
from django.conf import settings


def test_research_queue_is_configured():
    assert settings.RESEARCH_QUEUE == "research"
    assert "research" in settings.RQ_QUEUES


def test_research_limits_have_initial_values():
    assert settings.RESEARCH_SET_MAX_ITEMS == 500_000
    assert settings.RESEARCH_INLINE_SET_MAX_ITEMS == 1_000
    assert settings.RESEARCH_JOB_TIMEOUT > 0


def test_test_queue_runs_synchronously():
    for queue in ("default", "research"):
        assert settings.RQ_QUEUES[queue]["ASYNC"] is False
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run --group test pytest tests/settings/test_research_settings.py -v`
Expected: FAIL with `AttributeError: 'Settings' object has no attribute 'RESEARCH_QUEUE'`.

- [ ] **Step 3: Add settings**

In `config/settings/base.py`, after `RQ_SHOW_ADMIN_LINK`:

```python
# Research set materialization runs on its own queue so a 500k-row snapshot
# never delays alert delivery on ``default``.
RESEARCH_QUEUE = "research"
RQ_QUEUES["research"] = {
    "URL": REDIS_URL,
    "DEFAULT_TIMEOUT": 1800,
}

# Initial ceiling from the design; tune after research_benchmark results.
RESEARCH_SET_MAX_ITEMS = env.int("RESEARCH_SET_MAX_ITEMS", 500_000)
RESEARCH_INLINE_SET_MAX_ITEMS = env.int("RESEARCH_INLINE_SET_MAX_ITEMS", 1_000)
RESEARCH_JOB_TIMEOUT = env.int("RESEARCH_JOB_TIMEOUT", 1800)
```

In `config/settings/development.py`, `test.py` and `production.py`, each currently redefines `RQ_QUEUES`. Add the research entry to each dict, e.g.:

```python
RQ_QUEUES: dict[str, dict[str, Any]] = {  # type: ignore[no-redef]
    "default": {...},
    "research": {
        "URL": REDIS_URL,
        "DEFAULT_TIMEOUT": 1800,
        "ASYNC": True,  # False in test.py
    },
}
```

In both compose files, change the worker anchor command to:

```yaml
  command: python manage.py rqworker default research
```

- [ ] **Step 4: Verify the settings test passes**

Run: `uv run --group test pytest tests/settings/test_research_settings.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add config/settings docker-compose.yml docker-compose.production.yml tests/settings/test_research_settings.py
git commit -m "feat: add research queue and set limits"
```

---

### Task 5: `research_benchmark` management command

**Files:**
- Create: `searches/management/commands/research_benchmark.py`
- Test: `tests/searches/test_research_benchmark.py`
- Create: `docs/research-benchmark.md`

**Interfaces:**
- Consumes: `QuerySpec`, `build_where`.
- Produces: `python manage.py research_benchmark [--limit N] [--with-insert]`, printing one line per benchmark case with `execution_ms`, `rows`, and buffer counts; exits 0. `_benchmark_cases() -> list[tuple[str, QuerySpec]]` is importable for tests.

- [ ] **Step 1: Write the failing tests**

Create `tests/searches/test_research_benchmark.py`:

```python
from datetime import datetime, timezone
from io import StringIO
from unittest.mock import MagicMock, patch

from django.core.management import call_command

from searches.management.commands.research_benchmark import _benchmark_cases
from searches.queryspec import QuerySpec


class TestBenchmarkCases:
    def test_cases_cover_the_design_queries(self):
        cases = dict(_benchmark_cases())

        assert cases["housing_ca"].text == "housing"
        assert cases["housing_ca"].states == ("CA",)
        assert cases["housing_nationwide"].text == "housing"
        assert cases["five_word_phrase"].text.startswith('"')
        assert cases["housing_ca_modified"].modified_after is not None


class TestCommand:
    @patch("searches.management.commands.research_benchmark.connection")
    def test_runs_explain_for_each_case(self, mock_connection):
        mock_cursor = MagicMock()
        mock_connection.cursor.return_value.__enter__ = MagicMock(
            return_value=mock_cursor
        )
        mock_connection.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_cursor.fetchone.return_value = [
            {
                "Plan": {
                    "Execution Time": 12.5,
                    "Plan Rows": 10,
                    "Shared Hit Blocks": 3,
                    "Shared Read Blocks": 1,
                }
            }
        ]

        out = StringIO()
        call_command("research_benchmark", stdout=out)

        sql = mock_cursor.execute.call_args[0][0]
        assert "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)" in sql
        assert "housing_ca" in out.getvalue()
        assert "housing_nationwide" in out.getvalue()
        assert "12.5" in out.getvalue()

    @patch("searches.management.commands.research_benchmark.connection")
    def test_with_insert_runs_insert_select(self, mock_connection):
        mock_cursor = MagicMock()
        mock_connection.cursor.return_value.__enter__ = MagicMock(
            return_value=mock_cursor
        )
        mock_connection.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_cursor.fetchone.return_value = [
            {"Plan": {"Execution Time": 1.0, "Plan Rows": 1}, "Execution Time": 1.0}
        ]

        call_command("research_benchmark", "--with-insert", stdout=StringIO())

        executed = [call.args[0] for call in mock_cursor.execute.call_args_list]
        assert any("INSERT INTO research_bench_setitem" in sql for sql in executed)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --group test pytest tests/searches/test_research_benchmark.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement the command**

Create `searches/management/commands/research_benchmark.py`:

```python
"""Measure the queries Research sets will depend on.

Run against a production-sized database copy:

    python manage.py research_benchmark --limit 100 --with-insert

Every PR touching build_where or the materialization SQL should quote the
before/after numbers this prints.
"""

from datetime import datetime, timedelta, timezone

from django.core.management.base import BaseCommand
from django.db import connection

from searches.queryspec import QuerySpec, build_where


def _benchmark_cases() -> list[tuple[str, QuerySpec]]:
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=30)
    return [
        ("housing_ca", QuerySpec(text="housing", states=("CA",))),
        ("housing_nationwide", QuerySpec(text="housing")),
        ("five_word_phrase", QuerySpec(text='"public hearing on housing policy"')),
        (
            "housing_ca_modified",
            QuerySpec(text="housing", states=("CA",), modified_after=cutoff),
        ),
    ]


def _plan_json(cursor):
    row = cursor.fetchone()
    plan = row[0]
    if isinstance(plan, list):
        plan = plan[0]
    return plan


class Command(BaseCommand):
    help = "Benchmark the queries and insert path Research sets depend on."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=100)
        parser.add_argument(
            "--with-insert",
            action="store_true",
            help="Also time INSERT ... SELECT into a throwaway temp table.",
        )

    def handle(self, *args, **options):
        limit = options["limit"]
        rows = []
        for name, spec in _benchmark_cases():
            rows.append(self._run_case(name, spec, limit))

        if options["with_insert"]:
            rows.append(self._run_insert_case())

        self.stdout.write(
            f"{'case':<24} {'exec_ms':>10} {'plan_rows':>10} {'hit':>8} {'read':>8}"
        )
        for row in rows:
            self.stdout.write(
                f"{row['name']:<24} {row['exec_ms']:>10.2f}"
                f" {row['plan_rows']:>10} {row['hit']:>8} {row['read']:>8}"
            )

    def _run_case(self, name, spec, limit):
        where_sql, params = build_where(spec)
        sql = f"""
            EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)
            SELECT id FROM meetings_meetingpage mp
            WHERE {where_sql}
            ORDER BY paradedb.score(id) DESC, meeting_date DESC, id
            LIMIT %s
        """
        with connection.cursor() as cursor:
            cursor.execute(sql, params + [limit])
            plan = _plan_json(cursor)

        plan_data = plan["Plan"]
        return {
            "name": name,
            "exec_ms": plan.get("Execution Time", 0.0),
            "plan_rows": plan_data.get("Plan Rows", 0),
            "hit": plan_data.get("Shared Hit Blocks", 0),
            "read": plan_data.get("Shared Read Blocks", 0),
        }

    def _run_insert_case(self):
        _, spec = _benchmark_cases()[0]
        where_sql, params = build_where(spec)
        with connection.cursor() as cursor:
            cursor.execute("""
                CREATE TEMP TABLE IF NOT EXISTS research_bench_setitem (
                    result_set_id uuid,
                    page_id varchar(255),
                    rank double precision,
                    meeting_date date,
                    municipality_id uuid,
                    state varchar(64),
                    document_type varchar(32),
                    disposition char(1),
                    added_via varchar(16),
                    added_at timestamptz,
                    UNIQUE (result_set_id, page_id)
                )
                """)
            cursor.execute(
                f"""
                EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)
                INSERT INTO research_bench_setitem
                SELECT '00000000-0000-0000-0000-000000000001'::uuid,
                       mp.id, paradedb.score(mp.id), mp.meeting_date,
                       mp.municipality_id, mp.state, mp.document_type,
                       'u', 'query', now()
                FROM meetings_meetingpage mp
                WHERE {where_sql}
                ON CONFLICT DO NOTHING
                """,
                params,
            )
            plan = _plan_json(cursor)
            cursor.execute("DROP TABLE research_bench_setitem")

        plan_data = plan["Plan"]
        return {
            "name": "housing_ca_insert",
            "exec_ms": plan.get("Execution Time", 0.0),
            "plan_rows": plan_data.get("Actual Rows", plan_data.get("Plan Rows", 0)),
            "hit": plan_data.get("Shared Hit Blocks", 0),
            "read": plan_data.get("Shared Read Blocks", 0),
        }
```

Create `docs/research-benchmark.md` with a short, honest note:

```markdown
# Research benchmark (Gate A)

Run `python manage.py research_benchmark --limit 100 --with-insert` against a
production-sized database copy and paste the table here before setting or
raising `RESEARCH_SET_MAX_ITEMS`.

Open item from the design: confirm the out-of-band BM25 index actually covers
`modified` and the `build_where` filter columns. If it does not, the
`modified_after` search and filtered searches will fall back to a sequential
scan; add the column to the index or a concurrent btree before shipping sets.
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group test pytest tests/searches/test_research_benchmark.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add searches/management/commands/research_benchmark.py tests/searches/test_research_benchmark.py docs/research-benchmark.md
git commit -m "feat: add research query benchmark command"
```

---

## Phase 0 done when

- `QuerySpec` and `build_where` are the only place search predicates are built.
- `modified_after` filters on `MeetingPage.modified` inside SQL and in the cache key.
- Alert scans page through more than 10,000 ids and warn at the safety ceiling.
- The `research` queue exists in every environment and both compose workers run it.
- `research_benchmark` runs and its output is pasted into `docs/research-benchmark.md`.
- Full suite passes: `uv run --group test pytest`.
