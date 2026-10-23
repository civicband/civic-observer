# Fix Duplicate Immediate Notifications Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop concurrent workers from sending duplicate "New Results for <search>" emails for the same batch of new pages.

**Architecture:** Make `check_saved_search_for_updates` claim its cutoff window with an atomic conditional `UPDATE` (compare-and-swap). Only the worker that flips `last_checked_for_new_pages` from its observed value sends; a concurrent worker that read the same cutoff matches zero rows and skips.

**Tech Stack:** Django, PostgreSQL, django-rq, pytest.

**Spec:** N/A (bug report: duplicate immediate notification emails).

## Global Constraints

- Python >= 3.13; Django >= 5.2; codebase targets Python 3.12 syntax (ruff `target-version = py312`).
- Line length 88 (ruff).
- All test commands run through `uv run --group test pytest` (test deps live in the `test` dependency group, not `dev`).
- Local test DB: `DATABASE_URL="postgres://postgres@localhost:5434/postgres"` (the `civic-observer-testdb` container publishes 5434 with trust auth).
- Do not add comments to production code beyond the explanatory comments shown in this plan.
- Every production change must be preceded by a failing test (TDD).

## Review Focus

- A page ingested *during* the search must not be sent twice — result set is frozen (`list()`) before the claim.
- Digest-frequency searches must still be flagged `has_pending_results=True` (both workers setting `True` is harmless).
- Non-email channels (Discord/Slack/etc.) are deduped too, since the whole immediate branch is gated by the claim.
- The new `already_checked` status must not break `check_saved_searches` or admin callers.
- Admin "Check for new results" (synchronous, `searches/admin.py:174`) still notifies on genuinely new pages.

---

### Task 1: Atomic cutoff claim for immediate notifications

**Files:**
- Modify: `searches/tasks.py:10`, `:18-166`, `:321-345`, `:348-366`
- Test: `tests/searches/test_notification_tasks.py`

**Interfaces:**
- Produces: `check_saved_search_for_updates(saved_search_id)` may now return `{"status": "already_checked", ...}` in addition to `not_found | no_new_results | notified | pending`. `_format_channel_message` now accepts a `list` for `new_pages`.
- Consumes: existing `search_new_pages(search, cutoff) -> (QuerySet, int)`.

- [ ] **Step 1: Write the failing test**

Add to `tests/searches/test_notification_tasks.py` (inside `TestCheckSavedSearchesAfterIngest`):

```python
def test_concurrent_check_does_not_double_notify(self, monkeypatch):
    """
    If another worker advances the cutoff between our read and our claim,
    this worker must not send a duplicate notification.
    """
    from django.utils import timezone

    from searches import services
    from searches.models import SavedSearch
    from searches.tasks import check_saved_search_for_updates

    doc = MeetingDocumentFactory()
    search = SearchFactory(search_term="budget")
    search.municipalities.add(doc.municipality)
    saved_search = SavedSearchFactory(search=search, notification_frequency="immediate")
    MeetingPageFactory(document=doc, text="budget hearing")

    real = services.search_new_pages

    def racy(search_obj, cutoff):
        # Simulate a concurrent worker claiming this cutoff window first.
        SavedSearch.objects.filter(id=saved_search.id).update(
            last_checked_for_new_pages=timezone.now()
        )
        return real(search_obj, cutoff)

    monkeypatch.setattr(services, "search_new_pages", racy)

    result = check_saved_search_for_updates(saved_search.id)

    assert result["status"] == "already_checked"
    assert len(mail.outbox) == 0
```

- [ ] **Step 2: Run it to verify it fails**

Run: `DATABASE_URL="postgres://postgres@localhost:5434/postgres" uv run --group test pytest tests/searches/test_notification_tasks.py::TestCheckSavedSearchesAfterIngest::test_concurrent_check_does_not_double_notify -q --no-cov`
Expected: FAIL — `mail.outbox` has 1 message (current code sends unconditionally).

- [ ] **Step 3: Implement the claim**

In `searches/tasks.py`, change the model import to `from .models import SavedSearch, Search`.

Replace the body of `check_saved_search_for_updates` from `search = saved_search.search` through the `if not new_pages.exists():` block with the compare-and-swap claim shown in the task brief (frozen `list()`, conditional `UPDATE` on `prior_cutoff`, `Search` tracking update, `already_checked` early return).

Update `_format_channel_message` for a list (`len(new_pages)`, `new_pages[0]`), update the `check_saved_search_for_updates` docstring, and add an `already_checked` counter to `check_saved_searches` and its return dict.

- [ ] **Step 4: Run the targeted tests**

Run: `DATABASE_URL="postgres://postgres@localhost:5434/postgres" uv run --group test pytest tests/searches/test_notification_tasks.py -q --no-cov`
Expected: PASS.

- [ ] **Step 5: Verify no regressions across the suite**

Run: `DATABASE_URL="postgres://postgres@localhost:5434/postgres" uv run --group test pytest -q`
Expected: PASS.

- [ ] **Step 6: Lint and typecheck**

Run: `uv run --group dev ruff check . && uv run --group dev mypy .`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add searches/tasks.py tests/searches/test_notification_tasks.py
git commit -m "fix: prevent duplicate immediate notifications under concurrent workers"
```

---

## Out of scope (deferred)

- Daily/weekly digest **send** race (`searches/tasks.py:185-187`, `:299-303`).
- Meeting-digest race (`send_meeting_digests.py:140-181`).
- Webhook idempotency key and a persistent notification ledger.
- Duplicate *enqueue* of `check_saved_searches` per document type (benign once the claim exists).
