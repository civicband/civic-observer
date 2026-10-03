# Performance & Processing Bug Audit — Civic Observer

> **For agentic workers:** This document is both the audit and the implementation plan for the Critical tier. Critical tasks use `- [ ]` checkbox steps and are executed with superpowers:executing-plans or superpowers:subagent-driven-development. High/Medium/Low tiers are findings only (no steps yet).

**Goal:** Fix correctness/data-loss bugs and heavy query paths in the ingestion, search, notification, and webhook flows.

**Scope reviewed:** `meetings/`, `searches/`, `notifications/`, `municipalities/`, `notebooks/`, `clip/`, `apikeys/`, settings, migrations, RQ wiring.

**Verification note:** Findings are from static reading of `HEAD` (`f0a1d42`); the stack was not run. Severity reflects impact on data correctness first, then request/job cost.

---

## Critical — broken behavior / silent data loss

### C1. Immediate notifications are never sent automatically
- **Evidence:** `meetings/tasks.py:164-165` comment says completion handlers will enqueue `check_all_immediate_searches`, but neither `backfill_batch_task` (`:272`) nor `backfill_incremental_task` (`:180`) calls it. The webhook only calls `muni.update_searches()` (`municipalities/views.py:178`), which updates tracking fields and never notifies. `check_all_immediate_searches` only appears in tests and admin actions.
- **Impact:** Users on "immediate" frequency receive no email/channel notification when new matching pages are ingested by the normal webhook/backfill path.
- **Mitigation:** At the end of `backfill_batch_task` and `backfill_incremental_task` (and/or after webhook backfill enqueue completes), enqueue `check_all_immediate_searches` on the RQ queue. Add an integration test: ingest a page for a muni with an immediate SavedSearch and assert a notification is dispatched. Remove the stale comment. When scoping the check to a municipality, also include **all-municipality** (empty-scope) immediate searches, or they are silently excluded.

### C1a. Shared `Search` rows delivered immediate notifications per shared row, not per user (FIXED)
- **Evidence:** `Search` is shared across users (`SearchManager.get_or_create_for_params`, `searches/models.py:62-68`); `Search.update_search` mutated the shared `last_checked_for_new_pages`, and `get_new_pages` filtered on it. When multiple `SavedSearch` rows point at one `Search`, the first check advanced the shared cutoff and every later check saw no new pages. Reproduced with `tests/searches/test_notification_tasks.py::TestCheckAllSavedSearches::test_shared_search_notifies_each_saved_search` (1 email instead of 2).
- **Impact:** Immediate notifications for multi-user shared searches under-delivered — only one user per shared `Search` was notified.
- **Fix:** `check_all_immediate_searches` snapshots each `Search`'s cutoff *before* the loop and passes it through `check_saved_search_for_updates(..., since=...)` → `Search.update_search(since=...)` → `get_new_pages(..., since=...)`. Every saved search sharing a `Search` is evaluated against the same pre-batch cutoff. A never-checked Search uses an explicit earliest cutoff so the override is authoritative (passing `None` would fall back to the mutated value). Fixed by commit in the `fix/shared-search-notifications` branch.

### C4. Daily/weekly saved searches were never flagged for their digest automatically (FIXED)
- **Evidence:** `has_pending_results` was only set to `True` in `check_saved_search_for_updates` for non-immediate frequencies (`searches/tasks.py:89`). The only automatic caller, `check_all_immediate_searches`, filtered `notification_frequency="immediate"`. The remaining caller was the manual admin action (`searches/admin.py:238`). No scheduler exists in the repo.
- **Impact:** `send_daily_digests` / `send_weekly_digests` select on `has_pending_results=True`, so digest emails were effectively never triggered by ingestion — only by an admin manually marking pending. Daily/weekly subscribers got nothing.
- **Fix:** Renamed `check_all_immediate_searches` → `check_saved_searches` and dropped the immediate filter, so the post-ingest pass evaluates every saved search for the municipality: immediate → notify, daily/weekly → flag `has_pending_results`. It reuses the C1a snapshot cutoff, so a mixed-frequency shared `Search` notifies immediate subscribers and flags digest subscribers from the same new pages. Callers (`meetings.tasks._enqueue_saved_search_checks`) and tests updated. Covered by `test_digest_searches_are_flagged_after_ingest`, `test_shared_search_notifies_immediate_and_flags_digest`, and `test_only_immediate_searches_email_digests_are_flagged`.

### C2. HTTP errors silently mark full backfill "completed"
- **Evidence:** `meetings/services.py:179-186` catches `httpx.HTTPError`, increments `stats["errors"]`, and returns `next_cursor=None`. `backfill_batch_task` (`meetings/tasks.py:333-353`) treats `None` as "done", sets `status="completed"`, clears `force_full_backfill`, and never retries. JSON decode errors are *not* caught (inconsistent).
- **Impact:** A transient 5xx/timeout ends a full backfill as successful with missing data; the municipality is never re-queued.
- **Mitigation:** Re-raise (or return a distinct failure sentinel) on HTTP error so the task takes the failure path and lands as `failed` with `error_message`; add bounded retry/backoff. Wrap `response.json()` and raise `BackfillError` on decode failure. Test with a mocked 500 response.

### C3. Notification/digest logic is duplicated (verbatim)
- **Evidence:** `check_saved_search_for_updates`, `check_all_immediate_searches`, `send_daily_digests`, `send_weekly_digests`, `_send_digest_email`, `_send_to_notification_channels`, `_format_channel_message` exist in both `searches/tasks.py` and `notifications/services.py`. An AST-level comparison of the two copies at `6c73e71^` shows they were identical except for cosmetic import placement (`from searches.models import SavedSearch` at module level vs locally), a comment, and `SavedSearch = saved_searches[0].__class__` in the notifications copy (which lacked the module-level import) — no behavioral difference. Management commands, admin, and all tests import from `searches.tasks`; a scan of every local and remote branch found zero importers of the `notifications.services` copies, which only referenced each other.
- **Impact:** Two copies of the same logic; a fix applied to one would silently miss the other. Not currently diverging, but a latent regression risk.
- **Mitigation:** Consolidate into one module (keep `searches/tasks.py` since commands/tests import it), update imports, delete the duplicates. Done in Task 1. The retained `notifications.services` functions (`dispatch_notification`, `dispatch_to_all_channels`, `send_meeting_digest_email`) are unique to that module and still called.

---

## High — severe performance / correctness under load

### H1. Webhook runs all saved-search searches synchronously on every call
- **Evidence:** `municipalities/views.py:178` calls `muni.update_searches()` unconditionally, before the `should_backfill` check. `Muni.update_searches` (`municipalities/models.py:38-41`) iterates every Search and calls `update_search`, which executes two full search/count passes (`searches/models.py:157-177`).
- **Impact:** Every webhook (even with unchanged pages) blocks the request thread for 2×N searches across 15.5M rows. With many saved searches this is a request timeout / worker exhaustion vector.
- **Mitigation:** Only run when `should_backfill` is true, and enqueue it as an RQ job. Consider decoupling "recompute tracking" from notification detection, and adding a per-Search dirty/`last_checked` guard.

### H2. `execute_search` loads 10k IDs, then re-queries by `id__in`, twice per update
- **Evidence:** `searches/services.py:37-58` (`limit=10000`, then `MeetingPage.objects.filter(id__in=page_ids)`); `Search.update_search` calls both `get_new_pages` and `execute_search` again and `.count()` (`searches/models.py:168-172`).
- **Impact:** Up to 10k-element `IN` clause plus a full count per saved search, duplicated. Major DB cost in the digest/notification path.
- **Mitigation:** Collapse to one backend query; filter new pages on denormalized columns (`created__gte`, `municipality_id`, `state`, `document_type`, `meeting_date`) instead of `id__in`; reuse a single result set for the count.

### H3. Cache invalidation uses Redis `KEYS` (blocking O(N))
- **Evidence:** `searches/cache.py:194` and `:225` call `redis_conn.keys("civicobs:*:search:v1:*")` then delete.
- **Impact:** Run on every backfill completion; `KEYS` blocks Redis and scales poorly with cache size.
- **Mitigation:** Use `scan_iter` with a pipeline/batched `delete`, or version the cache namespace and bump a version key instead of scanning.

### H4. `rebackfill_failed_municipalities` aggregates the full 15.5M-row page table
- **Evidence:** `municipalities/management/commands/rebackfill_failed_municipalities.py:53-65` 3-table `LEFT JOIN` + `GROUP BY` counting `mp.id` for all municipalities.
- **Impact:** Multi-minute table scan/aggregation; run ad-hoc by operators.
- **Mitigation:** Count via the denormalized `meetingpage.municipality_id` (single indexed table) or use `Muni.pages`, or add a maintained counter. Avoid the join to `meetingdocument`.

### H5. Search filter columns have no btree indexes
- **Evidence:** `searches/search_backends.py:194-211` filters on `municipality_id`, `state`, `meeting_date`, `document_type`; `meetings/migrations/0010_...` removed the old indexes and the model comments (`meetings/models.py:110-117`) say the BM25 index covers them. No in-repo BM25 index definition exists to confirm.
- **Impact:** Filtered searches may fall back to scanning 15.5M rows; worst case every search is slow.
- **Mitigation:** Verify the out-of-band ParadeDB index definition; if filter columns aren't indexed, add `CREATE INDEX CONCURRENTLY` btree indexes (or include them in the BM25 index config). Add a query-plan test/benchmark.

### H6. Per-page `update_or_create` in backfill (no bulk)
- **Evidence:** `meetings/services.py:255-263`, `meetings/resilient_backfill.py:337-345`, and `clip/services.py:125-133`.
- **Impact:** 1–2 queries per page; millions of round trips for large backfills, contributing to RQ timeouts.
- **Mitigation:** Batch upsert (`bulk_create(..., update_conflicts=True)`) per document/batch; denormalize fields in bulk. Keep per-row error handling where needed.

---

## Medium

### M1. Orchestrator concurrency race
- **Evidence:** `meetings/tasks.py:68-121` sets/checks status inside `select_for_update`, but sets `status="in_progress"` and saves at `:125-129` / `:146-149` *after* the transaction/lock is released.
- **Impact:** Two concurrent orchestrator calls can both pass the guard and enqueue duplicate backfills.
- **Mitigation:** Perform the status transition and enqueue decision inside the locked transaction, or use a unique constraint / advisory lock on `(municipality, document_type)` for in-progress.

### M2. `last_indexed` never updated on the live backfill path
- **Evidence:** Only `meetings/services.py:73` sets it, and `backfill_municipality_meetings` has no callers; tasks never set it.
- **Impact:** `last_indexed` stays NULL, so `rebackfill_failed_municipalities --only-never-indexed` and dashboards treat everything as never indexed → repeated re-backfill.
- **Mitigation:** Update `muni.last_indexed` on successful completion in both task paths; remove or wire up the legacy function.

### M3. Email sent inside `transaction.atomic` gives false atomicity
- **Evidence:** `searches/tasks.py:271-285` and `notifications/services.py:381-396` call `msg.send()` then `bulk_update` inside `with transaction.atomic()`.
- **Impact:** External email can't roll back; if the DB update fails, the next run resends (duplicate digests). Conversely a send failure rolls back flags silently.
- **Mitigation:** Mark state with `update_fields` (e.g. `has_pending_results=False` + a sent-log idempotency key) before/independent of sending; send after commit; record failures for retry.

### M4. `_update_checkpoint` overcounts `pages_fetched`
- **Evidence:** `meetings/resilient_backfill.py:181` does `pages_fetched += self.batch_size` even for the final partial batch.
- **Impact:** Inflated stats/misleading verification.
- **Mitigation:** Accumulate `len(rows)`.

### M5. Meeting-digest N+1 queries
- **Evidence:** `notifications/management/commands/send_meeting_digests.py:133-145` queries `already_sent_today` and meetings per user.
- **Impact:** O(users) extra queries; grows with subscribers.
- **Mitigation:** Prefetch `last_digest_sent` state and batch meetings by `(user, date)` in one query.

### M6. API-key validation writes on every request
- **Evidence:** `apikeys/internal_views.py:78-79` saves `last_used_at` per validation.
- **Impact:** DB write per validation call; hot path under load.
- **Mitigation:** Throttle (update only if older than N seconds) or enqueue.

### M7. Public search page view counter write per request
- **Evidence:** `searches/views.py:364-365` increments and saves.
- **Impact:** Write per page view; lost updates under concurrency.
- **Mitigation:** `update(view_count=F("view_count") + 1)` or an async/batched counter.

---

## Low

- **L1. Health check leaks cursors / weak check** — `config/views.py:16`: `all(conn.cursor().execute("SELECT 1") ...)` never closes cursors and the generator truthiness is meaningless. Use `connection.ensure_connection()` / explicit close.
- **L2. Webhook docs vs code mismatch + non-constant-time compare** — `docs/webhook-api.md:20-21` claims unauthenticated calls are accepted when `WEBHOOK_SECRET` is unset, but `municipalities/views.py:127-128` fails closed (401). `views.py:139` uses `==`; use `hmac.compare_digest` and fix docs.
- **L3. Notification under-reporting past 10k** — `searches/services.py:48-52` only warns. Stream/paginate if exactness matters.
- **L4. Dead/stale code** — `searches/indexing.py` describes Meilisearch but models now use ParadeDB; `searches/__pycache__/query_parser.*` has no source. Remove or update.
- **L5. Test hygiene** — `municipalities/tests.py` `test_webhook_with_put_method` (`:331-348`) never deletes `WEBHOOK_SECRET`, leaking env between tests; several `@override_settings()` calls pass no args. Use a fixture for env cleanup.
- **L6. Bluesky sender logs in per message** — `notifications/senders/bluesky.py:47-48`; cache the authenticated client.
- **L7. `Search.update_search` full `save()`** — `searches/models.py:175`; use `update_fields`.

---

## Suggested fix order

1. **C1, C2** (data correctness / lost notifications).
2. **C3** (consolidate duplicated notification code).
3. **H1, H2, H3** (webhook + digest hot path).
4. **H4, H5, H6** (backfill/search scalability).
5. **M1–M7**, then **L1–L7**.

**Verification for every fix:** `uv run pytest`, `uv run --group dev ruff check .`, `uv run --group dev mypy .` (per `CLAUDE.md`/`justfile`).

---

# Critical-Tier Fix Plan (C1–C3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement task-by-task. Steps use `- [ ]` checkboxes.

**Goal:** Make immediate notifications actually fire after ingestion (C1), make HTTP failures fail the backfill instead of silently completing it (C2), and collapse the duplicated notification logic to one source of truth (C3).

**Architecture:** C3 is a pure refactor that removes the dead copies in `notifications/services.py` while preserving the live functions (`dispatch_notification`, `dispatch_to_all_channels`, `send_meeting_digest_email`). C1 adds a municipality scope to `check_all_immediate_searches` and enqueues it from the two backfill completion paths. C2 changes `_backfill_document_type` to raise `BackfillError` on HTTP/JSON failure so `backfill_batch_task`/`backfill_incremental_task` take their existing failure path.

**Tech Stack:** Django 5.2, PostgreSQL 17 + ParadeDB, django-rq/Redis, pytest + factory_boy.

**Spec:** This document (the audit above is the spec; the plan argues from it).

## Global Constraints

- Run tests with `uv run pytest` (Docker services `db` + `redis` up), lint with `uv run --group dev ruff check .`, types with `uv run --group dev mypy .`.
- Tests mock the search backend automatically via `tests/conftest.py::mock_search_backend` (ORM `icontains` fallback).
- RQ is synchronous in tests (`config/settings/test.py`, `ASYNC=False`), but new tests must still patch `django_rq.get_queue` to assert intent deterministically.
- Do NOT change search backend SQL or the `MeetingPage` denormalized schema in this tier.
- Every removed function must have no remaining importers; verify with `grep` before deleting.

## Review Focus

- HTTP 5xx on the **first** page vs a **later** page (later page must not lose the already-committed checkpoint).
- A 200 response with a non-JSON body.
- A municipality with **zero** immediate searches (enqueue must be a harmless no-op).
- Multiple `SavedSearch` rows whose `Search` shares one municipality (scoping must not send duplicate emails).
- Re-running a completion handler must not double-send (idempotency of `check_saved_search_for_updates` via `last_checked_for_new_pages`).

---

### Task 1: C3 — Collapse duplicated notification logic

**Files:**
- Modify: `notifications/services.py` (remove duplicate cluster, keep channel dispatch + `send_meeting_digest_email`)
- Verify importers: `searches/tasks.py:296` (lazy import of `dispatch_to_all_channels`), `notifications/management/commands/send_meeting_digests.py:13`, `tests/notifications/test_services.py:5`, `tests/notifications/test_digest_services.py:9`
- Test: `tests/searches/test_notification_tasks.py`, `tests/searches/test_integration.py`, `tests/notifications/test_services.py`, `tests/notifications/test_digest_services.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `notifications/services.py` exports exactly `dispatch_notification`, `dispatch_to_all_channels`, `send_meeting_digest_email` (plus helpers) — no `check_saved_search_for_updates`, `check_all_immediate_searches`, `send_daily_digests`, `send_weekly_digests`, `_send_digest_email`, `_send_to_notification_channels`, `_format_channel_message`.

- [ ] **Step 1: Confirm no live importers of the duplicate functions**

Run:
```bash
grep -rn "notifications.services import\|notifications\.services\." --exclude-dir=.venv --exclude-dir=.mypy_cache --exclude-dir=htmlcov --exclude-dir=.worktrees .
```
Expected: only `dispatch_notification`, `dispatch_to_all_channels`, `send_meeting_digest_email` are imported outside `notifications/services.py` itself.

- [ ] **Step 2: Run the affected suite green before the change**

Run: `uv run pytest tests/searches/test_notification_tasks.py tests/searches/test_integration.py tests/notifications/test_services.py tests/notifications/test_digest_services.py -q`
Expected: PASS (baseline).

- [ ] **Step 3: Delete the duplicate cluster from `notifications/services.py`**

Remove `check_saved_search_for_updates`, `check_all_immediate_searches`, `send_daily_digests`, `send_weekly_digests`, `_send_digest_email`, `_send_to_notification_channels`, `_format_channel_message`. Keep module docstring corrected, and keep imports still needed by the surviving functions (`EmailMultiAlternatives`, `get_template`, `render_to_string`, `settings`, `defaultdict`, `get_sender`, `timezone`). Remove now-unused `transaction` import if nothing else uses it.

- [ ] **Step 4: Re-run the affected suite**

Run: `uv run pytest tests/searches/test_notification_tasks.py tests/searches/test_integration.py tests/notifications/test_services.py tests/notifications/test_digest_services.py -q`
Expected: PASS, unchanged.

- [ ] **Step 5: Run lint + types**

Run: `uv run --group dev ruff check . && uv run --group dev mypy notifications searches`
Expected: PASS (will catch any unused imports).

- [ ] **Step 6: Commit**

```bash
git add notifications/services.py
git commit -m "refactor: remove duplicated notification/digest logic from notifications.services"
```

---

### Task 2: C1 — Scope and trigger immediate notification checks

**Files:**
- Modify: `searches/tasks.py` (`check_all_immediate_searches` scope param)
- Modify: `meetings/tasks.py` (enqueue helper + call from both completion paths; fix stale comment at `:164`)
- Test: `tests/searches/test_notification_tasks.py`, `meetings/tests/test_tasks.py`

**Interfaces:**
- Consumes: `check_all_immediate_searches(municipality_id=None) -> dict[str, int]`.
- Produces: `meetings.tasks._enqueue_immediate_search_checks(municipality_id) -> None` enqueues `searches.tasks.check_all_immediate_searches` with the muni id on the `"default"` queue; called at the end of `backfill_batch_task` (only when `next_cursor` is falsy) and at the end of `backfill_incremental_task`.

- [ ] **Step 1: Write the failing scope test**

Add to `tests/searches/test_notification_tasks.py`:
```python
def test_check_all_immediate_searches_scopes_to_one_municipality(self):
    from searches.tasks import check_all_immediate_searches
    from tests.factories import MeetingDocumentFactory, MeetingPageFactory, MuniFactory

    m1 = MuniFactory(subdomain="scope-city-1")
    m2 = MuniFactory(subdomain="scope-city-2")
    s1 = SearchFactory(search_term="budget")
    s1.municipalities.add(m1)
    SavedSearchFactory(search=s1, notification_frequency="immediate")
    s2 = SearchFactory(search_term="budget")
    s2.municipalities.add(m2)
    SavedSearchFactory(search=s2, notification_frequency="immediate")

    for muni in (m1, m2):
        doc = MeetingDocumentFactory(municipality=muni)
        MeetingPageFactory(document=doc, text="budget hearing")

    check_all_immediate_searches(municipality_id=m1.id)
    assert len(mail.outbox) == 1
```

- [ ] **Step 2: Run it — expect failure**

Run: `uv run pytest tests/searches/test_notification_tasks.py::TestCheckSavedSearchesAfterIngest::test_check_all_immediate_searches_scopes_to_one_municipality -q`
Expected: FAIL with `TypeError: check_all_immediate_searches() got an unexpected keyword argument 'municipality_id'`.

- [ ] **Step 3: Add the scope parameter**

In `searches/tasks.py`, change the signature and query:
```python
def check_all_immediate_searches(municipality_id=None) -> dict[str, int]:
    immediate_searches = SavedSearch.objects.filter(
        notification_frequency="immediate"
    ).select_related("search", "user")
    if municipality_id is not None:
        immediate_searches = immediate_searches.filter(
            search__municipalities__id=municipality_id
        ).distinct()
```
Update the docstring to note the optional scope.

- [ ] **Step 4: Run the scope test — expect pass**

Run: `uv run pytest tests/searches/test_notification_tasks.py::TestCheckSavedSearchesAfterIngest::test_check_all_immediate_searches_scopes_to_one_municipality -q`
Expected: PASS.

- [ ] **Step 5: Write the failing enqueue tests**

In `meetings/tests/test_tasks.py`, update `test_batch_task_completes_when_no_more_pages` to assert the completion enqueue, and add a notification assertion to the incremental test:
```python
# test_batch_task_completes_when_no_more_pages — replace
# mock_queue.enqueue.assert_not_called() with:
mock_queue.enqueue.assert_called_once()
enqueued = mock_queue.enqueue.call_args[0][0]
assert enqueued.__name__ == "check_all_immediate_searches"
```
```python
# test_incremental_backfill_uses_date_range — add @patch("meetings.tasks.django_rq.get_queue")
# to the decorators, accept mock_get_queue, and after backfill_incremental_task(...):
mock_get_queue.return_value.enqueue.assert_called_once()
assert (
    mock_get_queue.return_value.enqueue.call_args[0][0].__name__
    == "check_all_immediate_searches"
)
```

- [ ] **Step 6: Run them — expect failure**

Run: `uv run pytest meetings/tests/test_tasks.py -q`
Expected: FAIL (no notification enqueue yet).

- [ ] **Step 7: Add the helper and wire both completion paths**

In `meetings/tasks.py`, add near the top of the module (after `logger`):
```python
def _enqueue_immediate_search_checks(municipality_id) -> None:
    """Kick off immediate saved-search notifications for one municipality."""
    from searches.tasks import check_all_immediate_searches

    try:
        queue = django_rq.get_queue("default")
        queue.enqueue(check_all_immediate_searches, municipality_id)
    except Exception as e:
        logger.error(f"Failed to enqueue immediate search checks: {e}", exc_info=True)
```
Call it after cache invalidation in `backfill_incremental_task` (after `invalidate_search_cache_for_municipality(...)`, `meetings/tasks.py:244`) and in `backfill_batch_task` inside the `else:` completion branch after `invalidate_search_cache_for_municipality(...)` (`meetings/tasks.py:358`), passing `muni.id` (for incremental `int(muni.id)` is already used for invalidation; pass the same value). Replace the stale `:164-165` comment with one stating the completion handlers enqueue it.

- [ ] **Step 8: Run the tests — expect pass**

Run: `uv run pytest meetings/tests/test_tasks.py tests/searches/test_notification_tasks.py -q`
Expected: PASS.

- [ ] **Step 9: Add the idempotency test (Review Focus)**

Add to `tests/searches/test_notification_tasks.py`:
```python
def test_check_all_immediate_is_idempotent_across_runs(self):
    from searches.tasks import check_all_immediate_searches

    doc = MeetingDocumentFactory()
    search = SearchFactory(search_term="budget")
    search.municipalities.add(doc.municipality)
    SavedSearchFactory(search=search, notification_frequency="immediate")
    MeetingPageFactory(document=doc, text="budget hearing")

    check_all_immediate_searches(municipality_id=doc.municipality_id)
    check_all_immediate_searches(municipality_id=doc.municipality_id)
    assert len(mail.outbox) == 1
```

- [ ] **Step 10: Run it — expect pass (no code change)**

Run: `uv run pytest tests/searches/test_notification_tasks.py::TestCheckSavedSearchesAfterIngest::test_check_all_immediate_is_idempotent_across_runs -q`
Expected: PASS. If it FAILS with two emails, stop and debug `Search.update_search` timestamp handling before continuing.

- [ ] **Step 11: Lint, types, commit**

Run: `uv run --group dev ruff check . && uv run --group dev mypy meetings searches`
```bash
git add searches/tasks.py meetings/tasks.py meetings/tests/test_tasks.py tests/searches/test_notification_tasks.py
git commit -m "fix: trigger immediate saved-search notifications after backfill completes"
```

---

### Task 3: C2 — Fail loudly on HTTP/JSON errors during backfill

**Files:**
- Modify: `meetings/services.py` (`_backfill_document_type` error handling)
- Test: `meetings/tests/test_services.py`

**Interfaces:**
- Consumes: `BackfillError` (already defined in `meetings/services.py:20`).
- Produces: `_backfill_document_type` raises `BackfillError` on `httpx.HTTPError` and on non-JSON responses; the existing callers' `except Exception` paths in `backfill_batch_task`/`backfill_incremental_task` then mark `BackfillProgress.status="failed"` and do not clear `force_full_backfill`.

- [ ] **Step 1: Write the failing HTTP-error test**

Add to `meetings/tests/test_services.py`:
```python
import httpx
import pytest
from unittest.mock import patch

from meetings.services import BackfillError, _backfill_document_type


@pytest.mark.django_db
def test_backfill_document_type_raises_on_http_error():
    from municipalities.models import Muni

    muni = Muni.objects.create(subdomain="httpfail", name="HTTP Fail", state="CA")
    with patch.object(httpx.Client, "get", side_effect=httpx.ConnectError("boom")):
        with pytest.raises(BackfillError):
            _backfill_document_type(muni, "agendas", "agenda")


@pytest.mark.django_db
def test_backfill_document_type_raises_on_non_json_body():
    from municipalities.models import Muni

    muni = Muni.objects.create(subdomain="badjson", name="Bad JSON", state="CA")
    resp = type(
        "R",
        (),
        {
            "raise_for_status": lambda self: None,
            "json": lambda self: (_ for _ in ()).throw(ValueError("no json")),
        },
    )()
    with patch.object(httpx.Client, "get", return_value=resp):
        with pytest.raises(BackfillError):
            _backfill_document_type(muni, "agendas", "agenda")
```

- [ ] **Step 2: Run — expect failure**

Run: `uv run pytest meetings/tests/test_services.py -q`
Expected: FAIL (functions currently swallow the error and return `(stats, None)`).

- [ ] **Step 3: Raise instead of swallowing**

In `meetings/services.py::_backfill_document_type`:
```text
            while True:
                response = client.get(base_url, params=params)
                response.raise_for_status()
                try:
                    data = response.json()
                except ValueError as e:
                    raise BackfillError(
                        f"Non-JSON response fetching {table_name} for {muni.subdomain}: {e}"
                    ) from e
                ...
    except httpx.HTTPError as e:
        logger.error(
            f"HTTP error fetching {table_name} for {muni.subdomain}: {e}",
            exc_info=True,
        )
        raise BackfillError(
            f"HTTP error fetching {table_name} for {muni.subdomain}: {e}"
        ) from e
```
Remove the now-unreachable `stats["errors"] += 1` line in that handler.

- [ ] **Step 4: Run — expect pass**

Run: `uv run pytest meetings/tests/test_services.py -q`
Expected: PASS.

- [ ] **Step 5: Add the task-level regression test (Review Focus)**

In `meetings/tests/test_tasks.py`:
```python
@patch("meetings.tasks.django_rq.get_queue")
def test_batch_task_marks_failed_on_http_error(self, mock_get_queue):
    from meetings.services import BackfillError
    from meetings.tasks import backfill_batch_task

    mock_get_queue.return_value = Mock()
    muni = Muni.objects.create(subdomain="failcity", name="Fail City", state="CA")
    progress = BackfillProgress.objects.create(
        municipality=muni,
        document_type="agenda",
        mode="full",
        status="in_progress",
        force_full_backfill=True,
    )
    with patch(
        "meetings.services._backfill_document_type",
        side_effect=BackfillError("HTTP 500"),
    ):
        with pytest.raises(BackfillError):
            backfill_batch_task(muni.id, "agenda", progress.id)
    progress.refresh_from_db()
    assert progress.status == "failed"
    assert progress.force_full_backfill is True  # not cleared
```

- [ ] **Step 6: Run it — expect pass**

Run: `uv run pytest meetings/tests/test_tasks.py::TestBackfillBatchTask::test_batch_task_marks_failed_on_http_error -q`
Expected: PASS (existing failure path handles it).

- [ ] **Step 7: Lint, types, commit**

Run: `uv run --group dev ruff check . && uv run --group dev mypy meetings`
```bash
git add meetings/services.py meetings/tests/test_services.py meetings/tests/test_tasks.py
git commit -m "fix: fail backfill on HTTP/JSON errors instead of marking it complete"
```

---

## Self-Review

- **Spec coverage:** C1 → Task 2 (scope + enqueue + comment fix + idempotency). C2 → Task 3 (raise + task regression). C3 → Task 1 (delete duplicates). All three critical findings covered.
- **Placeholder scan:** no TBD/TODO; every code step shows the change.
- **Type consistency:** `check_all_immediate_searches(municipality_id=None)` used identically in tests and `_enqueue_immediate_search_checks`; `BackfillError` reused, not redefined.
- **Review Focus:** first/later-page HTTP error → Task 3 Step 1/5; non-JSON body → Task 3 Step 1; zero-immediate-search muni → covered by Task 2 Step 8 full-suite run; shared-municipality dedupe → Task 2 Step 1 uses `.distinct()`; re-run idempotency → Task 2 Step 9.
