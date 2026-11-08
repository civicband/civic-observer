# Research Projects — Phase 1B Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a signed-in researcher triage a materialized set: set one item's disposition, bulk-dispose a whole lens, undo one level by event, and review items one at a time with keyboard shortcuts.

**Architecture:** A small service layer (`research.audit`, `research.disposition`) owns every disposition mutation and its single `AuditEvent`. `SetItem.last_event` points at the event that last changed the row, and `previous_disposition` carries one level of undo. Views are server-rendered htmx partials, owner-scoped through `project__owner=request.user`. Review mode reuses the set-local browse ordering with a forward/back cursor.

**Tech Stack:** Django 5.2, PostgreSQL 17, htmx, Alpine, Tailwind, pytest, factory_boy.

**Spec:** `docs/superpowers/specs/2026-11-06-research-projects-design.md`

## Global Constraints

- **Choice values are lowercase strings** (`"u"`, `"r"`, `"i"`, `"query"`, `"lens"`, `"manual"`, `"import"`, `"new_matches"`) so raw SQL, JSON and the URL agree.
- **One event per decision.** A single disposition writes one `items.disposed` event; a bulk action writes one event for the whole lens, never one per item. Undo writes one `bulk.undone` event.
- **One level of undo.** `SetItem.previous_disposition` is overwritten by the next decision; undoing an event only touches rows whose `last_event` is that event.
- **Manual beats rules.** A manual disposition clears `disposition_by_rule`; the row becomes a manual decision.
- **Snapshot semantics.** Dispositions never change set membership; ignored items are hidden by the default lens, not deleted.
- **Owner scoping:** every view is `LoginRequiredMixin` and filters through `project__owner=request.user`; other users' projects/sets/items/events are 404.
- **Page references:** `SetItem.page` is `db_constraint=False, on_delete=DO_NOTHING`; the UI must tolerate a deleted page and render the "Page no longer in corpus" placeholder.
- **Set-local only:** bulk and review filters run against `research_setitem`, never the corpus.
- **Phase 1B excludes** bulk code application, code toggles in review, memos, ignore rules, and the "How we did this" summary — those land in Phase 1C/1D. Do not stub or half-build them.

## Review Focus

1. A disposition action on an item whose `MeetingPage` row was deleted must still update the item and render the placeholder card without a 500 (Tasks 1–2).
2. A bulk action over a lens with text/code/date filters must update only the items matching that lens, never the whole set (Task 1).
3. Undo must not touch an item that was changed again after the event being undone, and must restore each item's own prior disposition for a mixed-disposition bulk action (Task 1).
4. Undoing a bulk action must clear `ignore_reason` and `disposition_by_rule`, leaving the row as a manual decision (Task 1).
5. Review mode must not skip or duplicate items when moving forward and backward, including `rank IS NULL` and `date IS NULL` rows (Task 3).
6. A malformed `after`/`before` cursor or an invalid disposition must return a 4xx or reset to the first item, never a 500 (Tasks 2–3).
7. A bulk action must never update items in another set or another user's set (Task 2).

---

### Task 1: Disposition service, audit event and one-level undo

**Files:**
- Create: `research/audit.py`
- Create: `research/disposition.py`
- Modify: `research/browse.py` (make `filter_items` public; keep behavior identical)
- Test: `tests/research/test_disposition.py`

**Interfaces:**
- Consumes: `SetItem`, `AuditEvent`, `ResultSet` from `research.models`; `Lens` from `research.lens`; `filter_items` from `research.browse`.
- Produces:
  - `record_event(project, verb, *, actor=None, result_set=None, payload=None) -> AuditEvent`
  - `dispose_item(item, disposition, *, actor=None, reason="") -> AuditEvent | None`
  - `dispose_items(result_set, lens, disposition, *, actor=None, reason="") -> tuple[AuditEvent | None, int]`
  - `undo_disposition_event(event, *, actor=None) -> tuple[AuditEvent | None, int]`
  - `filter_items(result_set, lens) -> QuerySet[SetItem]` (public alias of the existing private helper)

- [ ] **Step 1: Write the failing tests**

Create `tests/research/test_disposition.py`:

```python
import pytest
from django.utils import timezone

from research.browse import filter_items
from research.disposition import (
    dispose_item,
    dispose_items,
    undo_disposition_event,
)
from research.lens import Lens
from research.models import AuditEvent, SetItem
from tests.factories import (
    IgnoreRuleFactory,
    ProjectFactory,
    ResultSetFactory,
    SetItemFactory,
    UserFactory,
)


@pytest.mark.django_db
class TestDisposeItem:
    def test_marks_relevant_and_records_one_event(self):
        item = SetItemFactory()
        actor = UserFactory()

        event = dispose_item(item, "r", actor=actor)
        item.refresh_from_db()

        assert item.disposition == "r"
        assert item.previous_disposition == "u"
        assert item.ignore_reason == ""
        assert item.last_event == event
        assert event.verb == "items.disposed"
        assert event.actor == actor
        assert event.result_set == item.result_set
        assert event.payload["item_ids"] == [item.id]
        assert event.payload["from"] == "u"
        assert event.payload["to"] == "r"
        assert event.payload["count"] == 1
        assert event.payload["reason"] == ""

    def test_ignore_records_reason(self):
        item = SetItemFactory()

        dispose_item(item, "i", reason="  Boilerplate  ")
        item.refresh_from_db()

        assert item.disposition == "i"
        assert item.ignore_reason == "Boilerplate"
        assert item.previous_disposition == "u"

    def test_manual_disposition_clears_rule(self):
        item = SetItemFactory()
        rule = IgnoreRuleFactory(project=item.result_set.project)
        item.disposition = "i"
        item.disposition_by_rule = rule
        item.save()

        dispose_item(item, "r")
        item.refresh_from_db()

        assert item.disposition == "r"
        assert item.disposition_by_rule is None
        assert item.ignore_reason == ""

    def test_unreviewed_clears_reason(self):
        item = SetItemFactory(disposition="i", ignore_reason="Boilerplate")

        dispose_item(item, "u")
        item.refresh_from_db()

        assert item.disposition == "u"
        assert item.ignore_reason == ""

    def test_noop_writes_no_event(self):
        item = SetItemFactory(disposition="u")

        event = dispose_item(item, "u")

        assert event is None
        assert not AuditEvent.objects.filter(verb="items.disposed").exists()

    def test_same_disposition_with_new_reason_writes_event(self):
        item = SetItemFactory(disposition="i", ignore_reason="Boilerplate")

        event = dispose_item(item, "i", reason="Off topic")

        assert event is not None
        item.refresh_from_db()
        assert item.ignore_reason == "Off topic"

    def test_invalid_disposition_raises(self):
        item = SetItemFactory()

        with pytest.raises(ValueError):
            dispose_item(item, "x")

    def test_reason_is_truncated_to_500_characters(self):
        item = SetItemFactory()

        dispose_item(item, "i", reason="x" * 600)
        item.refresh_from_db()

        assert len(item.ignore_reason) == 500

    def test_bumps_set_modified(self):
        item = SetItemFactory()
        before = item.result_set.modified

        dispose_item(item, "r")

        item.result_set.refresh_from_db()
        assert item.result_set.modified > before

    def test_missing_page_still_disposes(self):
        item = SetItemFactory()
        from meetings.models import MeetingPage

        MeetingPage.objects.filter(pk=item.page_id).delete()

        event = dispose_item(item, "r")
        item.refresh_from_db()

        assert event is not None
        assert item.disposition == "r"


@pytest.mark.django_db
class TestDisposeItems:
    def _three_items(self):
        result_set = ResultSetFactory()
        return result_set, [
            SetItemFactory(result_set=result_set, disposition="u"),
            SetItemFactory(result_set=result_set, disposition="u"),
            SetItemFactory(result_set=result_set, disposition="r"),
        ]

    def test_updates_only_lens_items(self):
        result_set, items = self._three_items()

        event, count = dispose_items(
            result_set, Lens(dispositions=("u",)), "i", reason="Boilerplate"
        )

        assert count == 2
        for item in items:
            item.refresh_from_db()
        assert items[0].disposition == "i"
        assert items[1].disposition == "i"
        assert items[2].disposition == "r"
        assert items[0].previous_disposition == "u"
        assert items[0].ignore_reason == "Boilerplate"
        assert event.payload["count"] == 2
        assert event.payload["from"] == {"u": 2}
        assert event.payload["lens"] == "disposition=u"

    def test_clears_rule_for_bulk(self):
        result_set, items = self._three_items()
        rule = IgnoreRuleFactory(project=result_set.project)
        items[0].disposition = "i"
        items[0].disposition_by_rule = rule
        items[0].save()

        dispose_items(result_set, Lens(dispositions=("i",)), "u")

        items[0].refresh_from_db()
        assert items[0].disposition == "u"
        assert items[0].disposition_by_rule is None

    def test_no_matches_writes_no_event(self):
        result_set = ResultSetFactory()

        event, count = dispose_items(result_set, Lens(dispositions=("i",)), "r")

        assert event is None
        assert count == 0
        assert not AuditEvent.objects.filter(verb="items.disposed").exists()

    def test_invalid_disposition_raises(self):
        result_set = ResultSetFactory()

        with pytest.raises(ValueError):
            dispose_items(result_set, Lens(), "x")

    def test_does_not_touch_other_sets(self):
        result_set = ResultSetFactory()
        other_set = ResultSetFactory(project=result_set.project)
        mine = SetItemFactory(result_set=result_set, disposition="u")
        other = SetItemFactory(result_set=other_set, disposition="u")

        dispose_items(result_set, Lens(dispositions=("u",)), "r")

        mine.refresh_from_db()
        other.refresh_from_db()
        assert mine.disposition == "r"
        assert other.disposition == "u"

    def test_bumps_set_modified(self):
        result_set = ResultSetFactory()
        SetItemFactory(result_set=result_set, disposition="u")
        before = result_set.modified

        dispose_items(result_set, Lens(dispositions=("u",)), "r")

        result_set.refresh_from_db()
        assert result_set.modified > before


@pytest.mark.django_db
class TestUndoDispositionEvent:
    def test_restores_previous_dispositions(self):
        result_set = ResultSetFactory()
        item_a = SetItemFactory(result_set=result_set, disposition="u")
        item_b = SetItemFactory(result_set=result_set, disposition="r")
        event, _ = dispose_items(result_set, Lens(), "i", reason="Boilerplate")

        undo, count = undo_disposition_event(event)

        item_a.refresh_from_db()
        item_b.refresh_from_db()
        assert count == 2
        assert item_a.disposition == "u"
        assert item_b.disposition == "r"
        assert item_a.previous_disposition == ""
        assert item_b.previous_disposition == ""
        assert item_a.ignore_reason == ""
        assert item_b.ignore_reason == ""
        assert item_a.disposition_by_rule is None
        assert undo.verb == "bulk.undone"
        assert undo.payload["original_event_id"] == event.id
        assert undo.payload["count"] == 2
        assert item_a.last_event == undo

    def test_does_not_touch_items_changed_after_event(self):
        result_set = ResultSetFactory()
        item = SetItemFactory(result_set=result_set, disposition="u")
        event, _ = dispose_items(result_set, Lens(), "i", reason="Boilerplate")
        dispose_item(item, "r")

        undo, count = undo_disposition_event(event)

        item.refresh_from_db()
        assert count == 0
        assert undo is None
        assert item.disposition == "r"

    def test_rejects_other_event_verbs(self):
        event = AuditEvent.objects.create(
            project=ProjectFactory(), verb="set.materialized"
        )

        with pytest.raises(ValueError):
            undo_disposition_event(event)

    def test_bumps_set_modified(self):
        result_set = ResultSetFactory()
        SetItemFactory(result_set=result_set, disposition="u")
        event, _ = dispose_items(result_set, Lens(), "r")
        before = timezone.now()

        undo_disposition_event(event)

        result_set.refresh_from_db()
        assert result_set.modified >= before


@pytest.mark.django_db
class TestFilterItems:
    def test_public_filter_matches_browse_defaults(self):
        result_set = ResultSetFactory()
        visible = SetItemFactory(result_set=result_set, disposition="u")
        ignored = SetItemFactory(result_set=result_set, disposition="i")

        assert list(filter_items(result_set, Lens())) == [visible]
        assert ignored not in list(filter_items(result_set, Lens()))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --group test pytest tests/research/test_disposition.py -q --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'research.disposition'` (and `filter_items` import failure).

- [ ] **Step 3: Make `filter_items` public in `research/browse.py`**

Rename the existing `_filtered_items` function to `filter_items` and update its two call sites (`browse_set` and `set_facets`). The function body stays exactly as it is today.

- [ ] **Step 4: Add `research/audit.py`**

```python
"""Append-only audit events for research decisions."""

from __future__ import annotations

from typing import Any

from .models import AuditEvent, Project, ResultSet


def record_event(
    project: Project,
    verb: str,
    *,
    actor=None,
    result_set: ResultSet | None = None,
    payload: dict[str, Any] | None = None,
) -> AuditEvent:
    """Write one append-only event and return it."""
    return AuditEvent.objects.create(
        project=project,
        actor=actor,
        verb=verb,
        result_set=result_set,
        payload=payload or {},
    )
```

- [ ] **Step 5: Add `research/disposition.py`**

```python
"""Disposition changes and one-level undo for research set items."""

from __future__ import annotations

from django.db.models import Count, F, QuerySet
from django.utils import timezone

from .audit import record_event
from .browse import filter_items
from .lens import Lens
from .models import AuditEvent, ResultSet, SetItem

MAX_REASON_LENGTH = 500


def _clean_reason(reason: str | None) -> str:
    return (reason or "").strip()[:MAX_REASON_LENGTH]


def _bump_set_modified(result_set_id) -> None:
    ResultSet.objects.filter(pk=result_set_id).update(modified=timezone.now())


def dispose_item(
    item: SetItem,
    disposition: str,
    *,
    actor=None,
    reason: str = "",
) -> AuditEvent | None:
    """Set one item's disposition and write one ``items.disposed`` event.

    Returns ``None`` when the call would not change the row.
    """
    if disposition not in SetItem.Disposition.values:
        raise ValueError(f"Invalid disposition: {disposition!r}")

    reason = _clean_reason(reason)
    if item.disposition == disposition and item.ignore_reason == reason:
        return None

    previous = item.disposition
    event = record_event(
        item.result_set.project,
        "items.disposed",
        actor=actor,
        result_set=item.result_set,
        payload={
            "item_ids": [item.id],
            "from": previous,
            "to": disposition,
            "reason": reason,
            "count": 1,
        },
    )
    item.previous_disposition = previous
    item.disposition = disposition
    item.ignore_reason = reason if disposition == SetItem.Disposition.IGNORED else ""
    item.disposition_by_rule = None
    item.last_event = event
    item.save(
        update_fields=[
            "previous_disposition",
            "disposition",
            "ignore_reason",
            "disposition_by_rule",
            "last_event",
        ]
    )
    _bump_set_modified(item.result_set_id)
    return event


def dispose_items(
    result_set: ResultSet,
    lens: Lens,
    disposition: str,
    *,
    actor=None,
    reason: str = "",
) -> tuple[AuditEvent | None, int]:
    """Bulk-dispose every item in ``lens`` with one UPDATE and one event."""
    if disposition not in SetItem.Disposition.values:
        raise ValueError(f"Invalid disposition: {disposition!r}")

    reason = _clean_reason(reason)
    items: QuerySet[SetItem] = filter_items(result_set, lens)
    counts = {
        row["disposition"]: row["count"]
        for row in items.values("disposition").annotate(count=Count("id"))
    }
    count = sum(counts.values())
    if count == 0:
        return None, 0

    event = record_event(
        result_set.project,
        "items.disposed",
        actor=actor,
        result_set=result_set,
        payload={
            "lens": lens.to_querystring(),
            "from": counts,
            "to": disposition,
            "reason": reason,
            "count": count,
        },
    )
    items.update(
        previous_disposition=F("disposition"),
        disposition=disposition,
        ignore_reason=reason if disposition == SetItem.Disposition.IGNORED else "",
        disposition_by_rule=None,
        last_event=event,
    )
    _bump_set_modified(result_set.pk)
    return event, count


def undo_disposition_event(
    event: AuditEvent, *, actor=None
) -> tuple[AuditEvent | None, int]:
    """Undo one ``items.disposed`` event one level deep."""
    if event.verb != "items.disposed":
        raise ValueError(f"Event cannot be undone: {event.verb!r}")

    items = SetItem.objects.filter(last_event=event).exclude(previous_disposition="")
    count = items.count()
    if count == 0:
        return None, 0

    undo_event = record_event(
        event.project,
        "bulk.undone",
        actor=actor,
        result_set=event.result_set,
        payload={"original_event_id": event.id, "count": count},
    )
    items.update(
        disposition=F("previous_disposition"),
        previous_disposition="",
        ignore_reason="",
        disposition_by_rule=None,
        last_event=undo_event,
    )
    if event.result_set_id:
        _bump_set_modified(event.result_set_id)
    return undo_event, count
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run --group test pytest tests/research/test_disposition.py -q --no-cov`
Expected: PASS.

- [ ] **Step 7: Run the research and searches suites**

Run: `uv run --group test pytest tests/research tests/searches -q --no-cov`
Expected: PASS (the `browse` rename must not change behavior).

- [ ] **Step 8: Commit**

```bash
git add research/audit.py research/disposition.py research/browse.py tests/research/test_disposition.py
git commit -m "feat: add disposition service with audit events and one-level undo"
```

---

### Task 2: Disposition, bulk and undo views with htmx partials

**Files:**
- Modify: `research/views.py`
- Modify: `research/urls.py`
- Create: `templates/research/partials/item.html`
- Create: `templates/research/partials/bulk_bar.html`
- Create: `templates/research/partials/bulk_confirm.html`
- Modify: `templates/research/partials/set_items.html`
- Modify: `templates/research/set_detail.html`
- Test: `tests/research/test_views.py`

**Interfaces:**
- Consumes: `dispose_item`, `dispose_items`, `undo_disposition_event` from Task 1; `filter_items`, `browse_set`, `set_facets` from `research.browse`; `Lens`; `ResultSet`, `SetItem`, `AuditEvent`.
- Produces URL names:
  - `research:item-disposition` → `POST <uuid:project>/sets/<uuid:set>/items/<int:item>/disposition/`
  - `research:set-bulk` → `POST <uuid:project>/sets/<uuid:set>/bulk/`
  - `research:event-undo` → `POST <uuid:project>/sets/<uuid:set>/events/<int:event>/undo/`
- Produces partials: `research/partials/item.html`, `research/partials/bulk_bar.html`, `research/partials/bulk_confirm.html`.
- `SetDetailView` gains `lens_count` in its context.

- [ ] **Step 1: Write the failing view tests**

Append to `tests/research/test_views.py`:

```python
@pytest.mark.django_db
class TestDispositionView:
    def _item(self):
        user = UserFactory()
        project = ProjectFactory(owner=user)
        result_set = ResultSetFactory(project=project)
        item = SetItemFactory(result_set=result_set)
        return user, result_set, item

    def _url(self, item):
        return reverse(
            "research:item-disposition",
            args=[item.result_set.project_id, item.result_set_id, item.id],
        )

    def test_requires_login(self, client):
        _, _, item = self._item()

        response = client.post(self._url(item), {"disposition": "r"})

        assert response.status_code == 302

    def test_cannot_dispose_another_users_item(self, client):
        _, _, item = self._item()
        client.force_login(UserFactory())

        response = client.post(self._url(item), {"disposition": "r"})

        assert response.status_code == 404

    def test_marks_relevant_and_returns_item_partial(self, client):
        user, result_set, item = self._item()
        client.force_login(user)

        response = client.post(self._url(item), {"disposition": "r", "lens": ""})

        assert response.status_code == 200
        item.refresh_from_db()
        assert item.disposition == "r"
        content = response.content.decode()
        assert "Relevant" in content
        assert "item-card" in content

    def test_ignore_records_reason(self, client):
        user, _, item = self._item()
        client.force_login(user)

        response = client.post(
            self._url(item), {"disposition": "i", "reason": "Boilerplate"}
        )

        assert response.status_code == 200
        item.refresh_from_db()
        assert item.disposition == "i"
        assert item.ignore_reason == "Boilerplate"

    def test_invalid_disposition_returns_400(self, client):
        user, _, item = self._item()
        client.force_login(user)

        response = client.post(self._url(item), {"disposition": "x"})

        assert response.status_code == 400

    def test_missing_page_still_returns_item_partial(self, client):
        user, _, item = self._item()
        client.force_login(user)
        from meetings.models import MeetingPage

        MeetingPage.objects.filter(pk=item.page_id).delete()

        response = client.post(self._url(item), {"disposition": "r"})

        assert response.status_code == 200
        assert "Page no longer in corpus" in response.content.decode()


@pytest.mark.django_db
class TestBulkActionView:
    def _set_with_items(self):
        user = UserFactory()
        project = ProjectFactory(owner=user)
        result_set = ResultSetFactory(project=project)
        unreviewed = SetItemFactory(result_set=result_set, disposition="u")
        relevant = SetItemFactory(result_set=result_set, disposition="r")
        return user, result_set, unreviewed, relevant

    def _url(self, result_set):
        return reverse("research:set-bulk", args=[result_set.project_id, result_set.id])

    def test_requires_login(self, client):
        _, result_set, _, _ = self._set_with_items()

        response = client.post(self._url(result_set), {"disposition": "r"})

        assert response.status_code == 302

    def test_cannot_bulk_another_users_set(self, client):
        _, result_set, _, _ = self._set_with_items()
        client.force_login(UserFactory())

        response = client.post(self._url(result_set), {"disposition": "r"})

        assert response.status_code == 404

    def test_shows_confirmation_count_without_confirm(self, client):
        user, result_set, unreviewed, relevant = self._set_with_items()
        client.force_login(user)

        response = client.post(
            self._url(result_set), {"disposition": "r", "lens": "disposition=u"}
        )

        assert response.status_code == 200
        content = response.content.decode()
        assert "1 item" in content
        unreviewed.refresh_from_db()
        relevant.refresh_from_db()
        assert unreviewed.disposition == "u"
        assert relevant.disposition == "r"

    def test_confirms_and_updates_lens_items(self, client):
        user, result_set, unreviewed, relevant = self._set_with_items()
        client.force_login(user)

        response = client.post(
            self._url(result_set),
            {"disposition": "r", "lens": "disposition=u", "confirm": "1"},
        )

        assert response.status_code == 200
        assert response["HX-Refresh"] == "true"
        unreviewed.refresh_from_db()
        relevant.refresh_from_db()
        assert unreviewed.disposition == "r"
        assert relevant.disposition == "r"

    def test_does_not_touch_other_sets(self, client):
        user, result_set, _, _ = self._set_with_items()
        other_set = ResultSetFactory(project=result_set.project)
        other_item = SetItemFactory(result_set=other_set, disposition="u")
        client.force_login(user)

        client.post(
            self._url(result_set),
            {"disposition": "r", "lens": "disposition=u", "confirm": "1"},
        )

        other_item.refresh_from_db()
        assert other_item.disposition == "u"

    def test_invalid_disposition_returns_400(self, client):
        user, result_set, _, _ = self._set_with_items()
        client.force_login(user)

        response = client.post(self._url(result_set), {"disposition": "x"})

        assert response.status_code == 400


@pytest.mark.django_db
class TestUndoView:
    def _event(self):
        user = UserFactory()
        project = ProjectFactory(owner=user)
        result_set = ResultSetFactory(project=project)
        item = SetItemFactory(result_set=result_set, disposition="u")
        from research.disposition import dispose_item

        event = dispose_item(item, "r", actor=user)
        return user, result_set, item, event

    def _url(self, event):
        return reverse(
            "research:event-undo",
            args=[event.project_id, event.result_set_id, event.id],
        )

    def test_requires_login(self, client):
        _, _, _, event = self._event()

        response = client.post(self._url(event))

        assert response.status_code == 302

    def test_cannot_undo_another_users_event(self, client):
        _, _, _, event = self._event()
        client.force_login(UserFactory())

        response = client.post(self._url(event))

        assert response.status_code == 404

    def test_restores_single_item_and_returns_partial(self, client):
        user, _, item, event = self._event()
        client.force_login(user)

        response = client.post(self._url(event), {"lens": ""})

        assert response.status_code == 200
        item.refresh_from_db()
        assert item.disposition == "u"
        assert "Unreviewed" in response.content.decode()

    def test_rejects_non_disposition_event(self, client):
        user = UserFactory()
        project = ProjectFactory(owner=user)
        result_set = ResultSetFactory(project=project)
        event = AuditEventFactory(
            project=project, result_set=result_set, verb="set.materialized"
        )
        client.force_login(user)

        response = client.post(
            reverse(
                "research:event-undo",
                args=[project.id, result_set.id, event.id],
            )
        )

        assert response.status_code == 400

    def test_bulk_undo_returns_refresh(self, client):
        user = UserFactory()
        project = ProjectFactory(owner=user)
        result_set = ResultSetFactory(project=project)
        SetItemFactory(result_set=result_set, disposition="u")
        from research.disposition import dispose_items

        event, _ = dispose_items(result_set, Lens(), "r", actor=user)
        client.force_login(user)

        response = client.post(
            reverse(
                "research:event-undo",
                args=[project.id, result_set.id, event.id],
            )
        )

        assert response.status_code == 200
        assert response["HX-Refresh"] == "true"
```

Add the missing imports at the top of `tests/research/test_views.py`:

```python
from research.disposition import dispose_item, dispose_items
from research.lens import Lens
from research.models import AuditEvent
from tests.factories import (
    AuditEventFactory,
    IgnoreRuleFactory,
    SetItemFactory,
)
```

(Only add imports not already present; keep the file's import block alphabetical.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --group test pytest tests/research/test_views.py -q --no-cov`
Expected: FAIL with `NoReverseMatch` for the new URL names.

- [ ] **Step 3: Add the views to `research/views.py`**

Add these imports:

```python
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.http import QueryDict

from meetings.models import MeetingPage

from .browse import browse_set, filter_items, set_facets
from .disposition import dispose_item, dispose_items, undo_disposition_event
from .models import AuditEvent, Project, ResultSet, SetItem
```

Replace the existing `from django.http import HttpResponse` and `from django.shortcuts import get_object_or_404, redirect` imports so there is only one import per module. Keep the rest.

Add the views after `SetStatusView`:

```python
class DispositionView(LoginRequiredMixin, View):
    def post(self, request, project, set, item):
        result_set = get_object_or_404(
            ResultSet,
            id=set,
            project_id=project,
            project__owner=request.user,
        )
        set_item = get_object_or_404(SetItem, id=item, result_set=result_set)
        disposition = request.POST.get("disposition", "")
        reason = request.POST.get("reason", "")
        lens = Lens.from_querydict(QueryDict(request.POST.get("lens", "")))
        try:
            dispose_item(set_item, disposition, actor=request.user, reason=reason)
        except ValueError:
            return HttpResponseBadRequest("Invalid disposition")

        set_item.refresh_from_db()
        set_item.page_obj = MeetingPage.objects.in_bulk([set_item.page_id]).get(
            set_item.page_id
        )
        context = {
            "result_set": result_set,
            "lens": lens,
            "querystring": lens.to_querystring(),
            "item": set_item,
        }
        return render(request, "research/partials/item.html", context)


class BulkActionView(LoginRequiredMixin, View):
    def post(self, request, project, set):
        result_set = get_object_or_404(
            ResultSet,
            id=set,
            project_id=project,
            project__owner=request.user,
        )
        disposition = request.POST.get("disposition", "")
        if disposition not in SetItem.Disposition.values:
            return HttpResponseBadRequest("Invalid disposition")
        reason = request.POST.get("reason", "")
        lens = Lens.from_querydict(QueryDict(request.POST.get("lens", "")))
        if request.POST.get("confirm") != "1":
            count = filter_items(result_set, lens).count()
            return render(
                request,
                "research/partials/bulk_confirm.html",
                {
                    "result_set": result_set,
                    "lens": lens,
                    "querystring": lens.to_querystring(),
                    "disposition": disposition,
                    "reason": reason,
                    "count": count,
                },
            )
        dispose_items(result_set, lens, disposition, actor=request.user, reason=reason)
        response = HttpResponse("")
        response["HX-Refresh"] = "true"
        return response


class UndoView(LoginRequiredMixin, View):
    def post(self, request, project, set, event):
        result_set = get_object_or_404(
            ResultSet,
            id=set,
            project_id=project,
            project__owner=request.user,
        )
        audit_event = get_object_or_404(
            AuditEvent,
            id=event,
            project=result_set.project,
            result_set=result_set,
        )
        try:
            undo_event, count = undo_disposition_event(audit_event, actor=request.user)
        except ValueError:
            return HttpResponseBadRequest("Event cannot be undone")
        if count == 0:
            return HttpResponse("Nothing to undo", status=409)

        item_ids = audit_event.payload.get("item_ids", [])
        if len(item_ids) != 1:
            response = HttpResponse("")
            response["HX-Refresh"] = "true"
            return response

        set_item = get_object_or_404(SetItem, id=item_ids[0], result_set=result_set)
        set_item.page_obj = MeetingPage.objects.in_bulk([set_item.page_id]).get(
            set_item.page_id
        )
        lens = Lens.from_querydict(QueryDict(request.POST.get("lens", "")))
        context = {
            "result_set": result_set,
            "lens": lens,
            "querystring": lens.to_querystring(),
            "item": set_item,
        }
        return render(request, "research/partials/item.html", context)
```

Add `lens_count` to `SetDetailView.get_context_data` inside the `if result_set.status == ResultSet.Status.READY:` block:

```python
context["lens_count"] = filter_items(result_set, lens).count()
```

- [ ] **Step 4: Add the URL routes to `research/urls.py`**

```python
path(
    "<uuid:project>/sets/<uuid:set>/items/<int:item>/disposition/",
    views.DispositionView.as_view(),
    name="item-disposition",
),
path(
    "<uuid:project>/sets/<uuid:set>/bulk/",
    views.BulkActionView.as_view(),
    name="set-bulk",
),
path(
    "<uuid:project>/sets/<uuid:set>/events/<int:event>/undo/",
    views.UndoView.as_view(),
    name="event-undo",
),
```

- [ ] **Step 5: Extract the item card into `templates/research/partials/item.html`**

Create the partial with the current card markup plus disposition controls. Use the existing classes from `set_items.html` so the visual style stays unchanged:

```html
<div class="item-card bg-white shadow rounded-lg border border-gray-200 p-6">
    <div class="flex items-start justify-between gap-4">
        <div class="flex-1 min-w-0">
            {% if item.page_obj %}
                <h3 class="text-lg font-semibold text-gray-900">{{ item.page_obj.meeting_name }}</h3>
                <div class="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-gray-500">
                    {% if item.page_obj.municipality_name %}
                        <span>{{ item.page_obj.municipality_name }}{% if item.state %}, {{ item.state }}{% endif %}</span>
                        <span>·</span>
                    {% endif %}
                    {% if item.meeting_date %}
                        <span>{{ item.meeting_date|date:"F j, Y" }}</span>
                        <span>·</span>
                    {% endif %}
                    <span>{{ item.document_type|default:item.page_obj.document_type|capfirst }}</span>
                    <span>·</span>
                    <span>Page {{ item.page_obj.page_number }}</span>
                </div>
            {% else %}
                <h3 class="text-lg font-semibold text-gray-900">Page no longer in corpus</h3>
                <div class="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-gray-500">
                    {% if item.state %}<span>{{ item.state }}</span><span>·</span>{% endif %}
                    {% if item.meeting_date %}<span>{{ item.meeting_date|date:"F j, Y" }}</span><span>·</span>{% endif %}
                    {% if item.document_type %}<span>{{ item.document_type|capfirst }}</span>{% endif %}
                </div>
            {% endif %}
        </div>
        <div class="flex-shrink-0">
            {% if item.disposition == "r" %}
                <span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-800">{{ item.get_disposition_display }}</span>
            {% elif item.disposition == "i" %}
                <span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-gray-100 text-gray-800">{{ item.get_disposition_display }}</span>
            {% else %}
                <span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-yellow-100 text-yellow-800">{{ item.get_disposition_display }}</span>
            {% endif %}
        </div>
    </div>
    {% if item.page_obj and item.page_obj.text %}
        <p class="mt-3 text-sm text-gray-700 leading-relaxed">{{ item.page_obj.text|truncatewords:50 }}</p>
    {% endif %}
    <div class="mt-4 flex flex-wrap items-center gap-2">
        <form hx-post="{% url 'research:item-disposition' result_set.project_id result_set.id item.id %}"
              hx-target="closest .item-card" hx-swap="outerHTML" class="inline-flex">
            {% csrf_token %}
            <input type="hidden" name="disposition" value="r">
            <input type="hidden" name="lens" value="{{ querystring }}">
            <button type="submit" class="inline-flex items-center px-3 py-1.5 border border-transparent text-xs font-medium rounded-md text-white bg-green-600 hover:bg-green-700">Relevant</button>
        </form>
        <form hx-post="{% url 'research:item-disposition' result_set.project_id result_set.id item.id %}"
              hx-target="closest .item-card" hx-swap="outerHTML" class="inline-flex items-center gap-2">
            {% csrf_token %}
            <input type="hidden" name="disposition" value="i">
            <input type="hidden" name="lens" value="{{ querystring }}">
            <input type="text" name="reason" value="{{ item.ignore_reason }}" placeholder="Reason (optional)"
                   class="rounded-md border-gray-300 text-xs shadow-sm focus:border-indigo-500 focus:ring-indigo-500">
            <button type="submit" class="inline-flex items-center px-3 py-1.5 border border-gray-300 text-xs font-medium rounded-md text-gray-700 bg-white hover:bg-gray-50">Ignore</button>
        </form>
        <form hx-post="{% url 'research:item-disposition' result_set.project_id result_set.id item.id %}"
              hx-target="closest .item-card" hx-swap="outerHTML" class="inline-flex">
            {% csrf_token %}
            <input type="hidden" name="disposition" value="u">
            <input type="hidden" name="lens" value="{{ querystring }}">
            <button type="submit" class="inline-flex items-center px-3 py-1.5 border border-gray-300 text-xs font-medium rounded-md text-gray-700 bg-white hover:bg-gray-50">Unreviewed</button>
        </form>
        {% if item.last_event_id %}
            <form hx-post="{% url 'research:event-undo' result_set.project_id result_set.id item.last_event_id %}"
                  hx-target="closest .item-card" hx-swap="outerHTML" class="inline-flex">
                {% csrf_token %}
                <input type="hidden" name="lens" value="{{ querystring }}">
                <button type="submit" class="inline-flex items-center px-3 py-1.5 border border-gray-300 text-xs font-medium rounded-md text-gray-700 bg-white hover:bg-gray-50">Undo</button>
            </form>
        {% endif %}
    </div>
</div>
```

Replace the card block in `templates/research/partials/set_items.html` with:

```html
{% for item in items %}
    {% include "research/partials/item.html" with item=item result_set=result_set querystring=querystring %}
{% endfor %}
```

Keep the surrounding `{% if items %}` wrapper, the "Next" link, and the empty state exactly as they are.

- [ ] **Step 6: Add the bulk bar and confirmation partials**

Create `templates/research/partials/bulk_bar.html`:

```html
{% if lens_count %}
    <div class="mb-4 flex flex-wrap items-center gap-3 rounded-lg border border-gray-200 bg-white p-3 shadow-sm">
        <span class="text-sm text-gray-600">{{ lens_count }} item{{ lens_count|pluralize }} in this view</span>
        <form hx-post="{% url 'research:set-bulk' result_set.project_id result_set.id %}"
              hx-target="#bulk-confirm" hx-swap="innerHTML" class="inline-flex">
            {% csrf_token %}
            <input type="hidden" name="lens" value="{{ querystring }}">
            <input type="hidden" name="disposition" value="r">
            <button type="submit" class="inline-flex items-center px-3 py-1.5 border border-transparent text-xs font-medium rounded-md text-white bg-green-600 hover:bg-green-700">Mark all relevant</button>
        </form>
        <form hx-post="{% url 'research:set-bulk' result_set.project_id result_set.id %}"
              hx-target="#bulk-confirm" hx-swap="innerHTML" class="inline-flex items-center gap-2">
            {% csrf_token %}
            <input type="hidden" name="lens" value="{{ querystring }}">
            <input type="hidden" name="disposition" value="i">
            <input type="text" name="reason" placeholder="Reason (optional)"
                   class="rounded-md border-gray-300 text-xs shadow-sm focus:border-indigo-500 focus:ring-indigo-500">
            <button type="submit" class="inline-flex items-center px-3 py-1.5 border border-gray-300 text-xs font-medium rounded-md text-gray-700 bg-white hover:bg-gray-50">Ignore all</button>
        </form>
    </div>
    <div id="bulk-confirm"></div>
{% endif %}
```

Create `templates/research/partials/bulk_confirm.html`:

```html
<div class="mt-4 rounded-lg border border-amber-200 bg-amber-50 p-4">
    <p class="text-sm text-amber-900">
        Change all {{ count }} item{{ count|pluralize }} in this view to "{{ disposition }}"?
    </p>
    <form hx-post="{% url 'research:set-bulk' result_set.project_id result_set.id %}"
          hx-target="#set-items" hx-swap="innerHTML" class="mt-3 flex gap-2">
        {% csrf_token %}
        <input type="hidden" name="lens" value="{{ querystring }}">
        <input type="hidden" name="disposition" value="{{ disposition }}">
        <input type="hidden" name="reason" value="{{ reason }}">
        <input type="hidden" name="confirm" value="1">
        <button type="submit" class="inline-flex items-center px-3 py-1.5 border border-transparent text-xs font-medium rounded-md text-white bg-indigo-600 hover:bg-indigo-700">Confirm</button>
        <button type="button" onclick="document.getElementById('bulk-confirm').innerHTML = ''"
                class="inline-flex items-center px-3 py-1.5 border border-gray-300 text-xs font-medium rounded-md text-gray-700 bg-white hover:bg-gray-50">Cancel</button>
    </form>
</div>
```

- [ ] **Step 7: Wire the bulk bar into `templates/research/set_detail.html`**

Inside the `lg:col-span-3` block, include the bulk bar and wrap the items in an id:

```html
<div class="lg:col-span-3">
    {% include "research/partials/bulk_bar.html" %}
    <div id="set-items">
        {% include "research/partials/set_items.html" %}
    </div>
</div>
```

- [ ] **Step 8: Run the view tests**

Run: `uv run --group test pytest tests/research/test_views.py -q --no-cov`
Expected: PASS for the new tests. Existing view tests still pass.

- [ ] **Step 9: Run the research suite**

Run: `uv run --group test pytest tests/research -q --no-cov`
Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add research/views.py research/urls.py templates/research/partials/item.html templates/research/partials/set_items.html templates/research/partials/bulk_bar.html templates/research/partials/bulk_confirm.html templates/research/set_detail.html tests/research/test_views.py
git commit -m "feat: add disposition, bulk action and undo views"
```

---

### Task 3: Review mode with forward/back cursor and keyboard shortcuts

**Files:**
- Modify: `research/browse.py` (add `review_item`, reverse cursor helpers)
- Modify: `research/views.py` (add `ReviewView`)
- Modify: `research/urls.py` (add `research:set-review`)
- Create: `templates/research/review.html`
- Create: `templates/research/partials/review_item.html`
- Modify: `templates/research/set_detail.html` (add the Review link in the header row)
- Test: `tests/research/test_browse.py`, `tests/research/test_views.py`

**Interfaces:**
- Consumes: `filter_items`, `_ordered`, `_apply_cursor`, `_encode_cursor` from Task 1; `Lens`; `MeetingPage`.
- Produces:
  - `review_item(result_set, lens, *, after=None, before=None) -> tuple[SetItem | None, str | None]`
  - URL name `research:set-review` → `GET <uuid:project>/sets/<uuid:set>/review/`
  - Partials `research/review.html`, `research/partials/review_item.html`.

- [ ] **Step 1: Write the failing browse tests**

Append to `tests/research/test_browse.py`:

```python
@pytest.mark.django_db
class TestReviewItem:
    def _set(self):
        result_set = ResultSetFactory()
        items = [
            SetItemFactory(result_set=result_set, rank=3.0),
            SetItemFactory(result_set=result_set, rank=2.0),
            SetItemFactory(result_set=result_set, rank=1.0),
        ]
        return result_set, items

    def test_returns_first_item_without_cursor(self):
        result_set, items = self._set()

        item, cursor = review_item(result_set, Lens())

        assert item == items[0]
        assert cursor == _encode_cursor(items[0], "rank")

    def test_after_moves_forward(self):
        result_set, items = self._set()

        item, cursor = review_item(
            result_set, Lens(), after=_encode_cursor(items[0], "rank")
        )

        assert item == items[1]
        assert cursor == _encode_cursor(items[1], "rank")

    def test_before_moves_backward(self):
        result_set, items = self._set()

        item, cursor = review_item(
            result_set, Lens(), before=_encode_cursor(items[1], "rank")
        )

        assert item == items[0]
        assert cursor == _encode_cursor(items[0], "rank")

    def test_before_from_first_item_returns_none(self):
        result_set, items = self._set()

        item, cursor = review_item(
            result_set, Lens(), before=_encode_cursor(items[0], "rank")
        )

        assert item is None
        assert cursor is None

    def test_after_from_last_item_returns_none(self):
        result_set, items = self._set()

        item, cursor = review_item(
            result_set, Lens(), after=_encode_cursor(items[-1], "rank")
        )

        assert item is None
        assert cursor is None

    def test_rank_null_forward_and_backward(self):
        result_set = ResultSetFactory()
        ranked = SetItemFactory(result_set=result_set, rank=1.0)
        unranked = SetItemFactory(result_set=result_set, rank=None)

        forward, _ = review_item(
            result_set, Lens(), after=_encode_cursor(ranked, "rank")
        )
        backward, _ = review_item(
            result_set, Lens(), before=_encode_cursor(unranked, "rank")
        )

        assert forward == unranked
        assert backward == ranked

    def test_date_asc_forward_and_backward(self):
        result_set = ResultSetFactory()
        older = SetItemFactory(result_set=result_set, meeting_date=date(2024, 1, 1))
        newer = SetItemFactory(result_set=result_set, meeting_date=date(2024, 6, 1))
        lens = Lens(order="date_asc")

        forward, _ = review_item(
            result_set, lens, after=_encode_cursor(older, "date_asc")
        )
        backward, _ = review_item(
            result_set, lens, before=_encode_cursor(newer, "date_asc")
        )

        assert forward == newer
        assert backward == older

    def test_date_null_forward_and_backward(self):
        result_set = ResultSetFactory()
        dated = SetItemFactory(result_set=result_set, meeting_date=date(2024, 1, 1))
        undated = SetItemFactory(result_set=result_set, meeting_date=None)
        lens = Lens(order="date_desc")

        forward, _ = review_item(
            result_set, lens, after=_encode_cursor(dated, "date_desc")
        )
        backward, _ = review_item(
            result_set, lens, before=_encode_cursor(undated, "date_desc")
        )

        assert forward == undated
        assert backward == dated

    def test_rejects_after_and_before_together(self):
        result_set = ResultSetFactory()

        with pytest.raises(ValueError):
            review_item(result_set, Lens(), after="1|a", before="2|b")

    def test_rejects_malformed_cursor(self):
        result_set = ResultSetFactory()

        with pytest.raises(ValueError):
            review_item(result_set, Lens(), after="not-a-cursor")

    def test_handles_missing_page(self):
        result_set = ResultSetFactory()
        item = SetItemFactory(result_set=result_set)
        from meetings.models import MeetingPage

        MeetingPage.objects.filter(pk=item.page_id).delete()

        found, _ = review_item(result_set, Lens())

        assert found == item
        assert found.page_obj is None
```

Add the imports to `tests/research/test_browse.py`:

```python
from research.browse import _encode_cursor, review_item
```

(`_encode_cursor` is private; the tests import it deliberately to build cursors. Keep the existing `browse_set, set_facets` import.)

- [ ] **Step 2: Run the browse tests to verify they fail**

Run: `uv run --group test pytest tests/research/test_browse.py::TestReviewItem -q --no-cov`
Expected: FAIL with `ImportError: cannot import name 'review_item'`.

- [ ] **Step 3: Add the review helpers to `research/browse.py`**

After `_apply_cursor`, add:

```python
def _reverse_ordered(items, order: str):
    if order == "date_asc":
        return items.order_by(
            F("meeting_date").desc(nulls_first=True), F("page_id").desc()
        )
    if order == "date_desc":
        return items.order_by(
            F("meeting_date").asc(nulls_first=True), F("page_id").desc()
        )
    return items.order_by(F("rank").asc(nulls_first=True), F("page_id").desc())


def _apply_cursor_before(items, before: str, order: str):
    first, separator, page_id = before.partition("|")
    if not separator:
        raise ValueError(f"Invalid cursor: {before!r}")

    if order.startswith("date"):
        if first:
            date_value = date.fromisoformat(first)
            if order == "date_asc":
                condition = Q(meeting_date__lt=date_value) | (
                    Q(meeting_date=date_value) & Q(page__lt=page_id)
                )
            else:
                condition = Q(meeting_date__gt=date_value) | (
                    Q(meeting_date=date_value) & Q(page__lt=page_id)
                )
        else:
            condition = Q(meeting_date__isnull=True, page__lt=page_id) | Q(
                meeting_date__isnull=False
            )
    elif first:
        rank_value = float(first)
        condition = Q(rank__gt=rank_value) | (Q(rank=rank_value) & Q(page__lt=page_id))
    else:
        condition = Q(rank__isnull=True, page__lt=page_id) | Q(rank__isnull=False)

    return _reverse_ordered(items.filter(condition), order)


def review_item(
    result_set, lens: Lens, *, after: str | None = None, before: str | None = None
) -> tuple[SetItem | None, str | None]:
    """Return one item and its cursor for keyboard review navigation."""
    if after and before:
        raise ValueError("after and before are mutually exclusive")

    items = filter_items(result_set, lens)
    if after:
        items = _apply_cursor(_ordered(items, lens.order), after, lens.order)
    elif before:
        items = _apply_cursor_before(items, before, lens.order)
    else:
        items = _ordered(items, lens.order)

    row = items.first()
    if row is None:
        return None, None

    row.page_obj = MeetingPage.objects.in_bulk([row.page_id]).get(row.page_id)
    return row, _encode_cursor(row, lens.order)
```

- [ ] **Step 4: Run the browse tests to verify they pass**

Run: `uv run --group test pytest tests/research/test_browse.py -q --no-cov`
Expected: PASS.

- [ ] **Step 5: Write the failing review view tests**

Append to `tests/research/test_views.py`:

```python
@pytest.mark.django_db
class TestReviewView:
    def _set_with_items(self):
        user = UserFactory()
        project = ProjectFactory(owner=user)
        result_set = ResultSetFactory(project=project)
        first = SetItemFactory(
            result_set=result_set,
            rank=2.0,
            page=MeetingPageFactory(text="first page text"),
        )
        second = SetItemFactory(
            result_set=result_set,
            rank=1.0,
            page=MeetingPageFactory(text="second page text"),
        )
        return user, result_set, first, second

    def _url(self, result_set):
        return reverse(
            "research:set-review", args=[result_set.project_id, result_set.id]
        )

    def test_requires_login(self, client):
        _, result_set, _, _ = self._set_with_items()

        response = client.get(self._url(result_set))

        assert response.status_code == 302

    def test_cannot_review_another_users_set(self, client):
        _, result_set, _, _ = self._set_with_items()
        client.force_login(UserFactory())

        response = client.get(self._url(result_set))

        assert response.status_code == 404

    def test_shows_first_item(self, client):
        user, result_set, first, _ = self._set_with_items()
        client.force_login(user)

        response = client.get(self._url(result_set))

        assert response.status_code == 200
        assert "review-card" in response.content.decode()

    def test_after_moves_to_next_item(self, client):
        user, result_set, first, second = self._set_with_items()
        client.force_login(user)
        from research.browse import _encode_cursor

        response = client.get(
            self._url(result_set),
            {"after": _encode_cursor(first, "rank")},
        )

        assert response.status_code == 200
        assert "second page text" in response.content.decode()

    def test_before_moves_to_previous_item(self, client):
        user, result_set, first, second = self._set_with_items()
        client.force_login(user)
        from research.browse import _encode_cursor

        response = client.get(
            self._url(result_set),
            {"before": _encode_cursor(second, "rank")},
        )

        assert response.status_code == 200
        assert "first page text" in response.content.decode()

    def test_malformed_cursor_resets_to_first_item(self, client):
        user, result_set, first, _ = self._set_with_items()
        client.force_login(user)

        response = client.get(self._url(result_set), {"after": "not-a-cursor"})

        assert response.status_code == 200
        assert "review-card" in response.content.decode()

    def test_empty_lens_shows_empty_state(self, client):
        user, result_set, _, _ = self._set_with_items()
        result_set.items.update(disposition="i")
        client.force_login(user)

        response = client.get(self._url(result_set))

        assert response.status_code == 200
        assert "No items left" in response.content.decode()

    def test_disposition_next_returns_next_item(self, client):
        user, result_set, first, second = self._set_with_items()
        client.force_login(user)
        from research.browse import _encode_cursor

        response = client.post(
            reverse(
                "research:item-disposition",
                args=[result_set.project_id, result_set.id, first.id],
            ),
            {"disposition": "r", "lens": "", "next": "review"},
        )

        assert response.status_code == 200
        assert "second page text" in response.content.decode()

    def test_undo_next_returns_item(self, client):
        user, result_set, first, _ = self._set_with_items()
        client.force_login(user)
        from research.disposition import dispose_item

        event = dispose_item(first, "r", actor=user)
        response = client.post(
            reverse(
                "research:event-undo",
                args=[result_set.project_id, result_set.id, event.id],
            ),
            {"lens": "", "next": "review"},
        )

        assert response.status_code == 200
        content = response.content.decode()
        assert "review-card" in content
        assert "first page text" in content
```

- [ ] **Step 6: Run the review view tests to verify they fail**

Run: `uv run --group test pytest tests/research/test_views.py::TestReviewView -q --no-cov`
Expected: FAIL with `NoReverseMatch` for `research:set-review`.

- [ ] **Step 7: Add the review branches to `research/views.py`**

Add `_encode_cursor` and `review_item` to the `research.browse` import line.

In `DispositionView.post`, before the final `return render(request, "research/partials/item.html", context)`, add:

```python
if request.POST.get("next") == "review":
    try:
        next_item, cursor = review_item(
            result_set, lens, after=_encode_cursor(set_item, lens.order)
        )
    except ValueError:
        next_item, cursor = review_item(result_set, lens)
    context["item"] = next_item
    context["cursor"] = cursor
    return render(request, "research/partials/review_item.html", context)
```

In `UndoView.post`, before the final `return render(request, "research/partials/item.html", context)`, add:

```python
if request.POST.get("next") == "review":
    context["cursor"] = _encode_cursor(set_item, lens.order)
    return render(request, "research/partials/review_item.html", context)
```

Add `ReviewView` after `UndoView`:

```python
class ReviewView(LoginRequiredMixin, View):
    def get(self, request, project, set):
        result_set = get_object_or_404(
            ResultSet,
            id=set,
            project_id=project,
            project__owner=request.user,
        )
        lens = Lens.from_querydict(request.GET)
        after = request.GET.get("after") or None
        before = request.GET.get("before") or None
        try:
            item, cursor = review_item(result_set, lens, after=after, before=before)
        except ValueError:
            item, cursor = review_item(result_set, lens)
        return render(
            request,
            "research/review.html",
            {
                "result_set": result_set,
                "lens": lens,
                "querystring": lens.to_querystring(),
                "item": item,
                "cursor": cursor,
            },
        )
```

- [ ] **Step 8: Add the review URL to `research/urls.py`**

```python
path(
    "<uuid:project>/sets/<uuid:set>/review/",
    views.ReviewView.as_view(),
    name="set-review",
),
```

- [ ] **Step 9: Create the review templates**

Create `templates/research/review.html`:

```html
{% extends "base.html" %}

{% block title %}Review {{ result_set.name }} - Research - CivicObserver{% endblock %}

{% block content %}
    <div class="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8 py-8"
         x-data="reviewKeyboard()" @keydown.window="handle($event)">
        <nav class="text-sm text-gray-500 mb-2">
            <a href="{% url 'research:project-list' %}" class="hover:text-indigo-600">Research</a>
            <span class="mx-2">/</span>
            <a href="{% url 'research:project-detail' result_set.project_id %}" class="hover:text-indigo-600">{{ result_set.project.name }}</a>
            <span class="mx-2">/</span>
            <a href="{% url 'research:set-detail' result_set.project_id result_set.id %}" class="hover:text-indigo-600">{{ result_set.name }}</a>
            <span class="mx-2">/</span>
            <span class="text-gray-900">Review</span>
        </nav>

        <div class="flex items-center justify-between">
            <h1 class="text-2xl font-bold tracking-tight text-gray-900">Review</h1>
            <p class="text-sm text-gray-500">j/k move · r relevant · i ignore · u unreviewed</p>
        </div>

        <div class="mt-6">
            {% include "research/partials/review_item.html" %}
        </div>
    </div>

    <script>
        function reviewKeyboard() {
            return {
                handle(event) {
                    if (event.target.tagName === "INPUT" || event.target.tagName === "TEXTAREA") {
                        return;
                    }
                    const targets = {
                        j: "review-next",
                        k: "review-prev",
                        r: "review-relevant",
                        i: "review-ignore",
                        u: "review-unreviewed",
                    };
                    const target = targets[event.key.toLowerCase()];
                    if (target) {
                        const element = document.getElementById(target);
                        if (element) {
                            event.preventDefault();
                            element.click();
                        }
                    }
                },
            };
        }
    </script>
{% endblock content %}
```

Add the Review link to the header row in `templates/research/set_detail.html`:

```html
<a href="{% url 'research:set-review' result_set.project_id result_set.id %}{% if querystring %}?{{ querystring }}{% endif %}"
   class="inline-flex items-center px-4 py-2 border border-gray-300 text-sm font-medium rounded-md shadow-sm text-gray-700 bg-white hover:bg-gray-50">
    Review
</a>
```

Create `templates/research/partials/review_item.html`:

```html
<div id="review-card" class="item-card bg-white shadow rounded-lg border border-gray-200 p-6">
    {% if item %}
        <div class="flex items-start justify-between gap-4">
            <div class="flex-1 min-w-0">
                {% if item.page_obj %}
                    <h3 class="text-lg font-semibold text-gray-900">{{ item.page_obj.meeting_name }}</h3>
                    <div class="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-gray-500">
                        {% if item.page_obj.municipality_name %}
                            <span>{{ item.page_obj.municipality_name }}{% if item.state %}, {{ item.state }}{% endif %}</span>
                            <span>·</span>
                        {% endif %}
                        {% if item.meeting_date %}
                            <span>{{ item.meeting_date|date:"F j, Y" }}</span>
                            <span>·</span>
                        {% endif %}
                        <span>{{ item.document_type|default:item.page_obj.document_type|capfirst }}</span>
                        <span>·</span>
                        <span>Page {{ item.page_obj.page_number }}</span>
                    </div>
                {% else %}
                    <h3 class="text-lg font-semibold text-gray-900">Page no longer in corpus</h3>
                    <div class="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-gray-500">
                        {% if item.state %}<span>{{ item.state }}</span><span>·</span>{% endif %}
                        {% if item.meeting_date %}<span>{{ item.meeting_date|date:"F j, Y" }}</span><span>·</span>{% endif %}
                        {% if item.document_type %}<span>{{ item.document_type|capfirst }}</span>{% endif %}
                    </div>
                {% endif %}
            </div>
            <div class="flex-shrink-0">
                {% if item.disposition == "r" %}
                    <span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-800">{{ item.get_disposition_display }}</span>
                {% elif item.disposition == "i" %}
                    <span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-gray-100 text-gray-800">{{ item.get_disposition_display }}</span>
                {% else %}
                    <span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-yellow-100 text-yellow-800">{{ item.get_disposition_display }}</span>
                {% endif %}
            </div>
        </div>
        {% if item.page_obj and item.page_obj.text %}
            <p class="mt-3 text-sm text-gray-700 leading-relaxed">{{ item.page_obj.text }}</p>
        {% endif %}
        <div class="mt-4 flex flex-wrap items-center gap-2">
            <form hx-post="{% url 'research:item-disposition' result_set.project_id result_set.id item.id %}"
                  hx-target="#review-card" hx-swap="outerHTML" class="inline-flex">
                {% csrf_token %}
                <input type="hidden" name="disposition" value="r">
                <input type="hidden" name="lens" value="{{ querystring }}">
                <input type="hidden" name="next" value="review">
                <button id="review-relevant" type="submit" class="inline-flex items-center px-3 py-1.5 border border-transparent text-xs font-medium rounded-md text-white bg-green-600 hover:bg-green-700">Relevant</button>
            </form>
            <form hx-post="{% url 'research:item-disposition' result_set.project_id result_set.id item.id %}"
                  hx-target="#review-card" hx-swap="outerHTML" class="inline-flex items-center gap-2">
                {% csrf_token %}
                <input type="hidden" name="disposition" value="i">
                <input type="hidden" name="lens" value="{{ querystring }}">
                <input type="hidden" name="next" value="review">
                <input type="text" name="reason" placeholder="Reason (optional)"
                       class="rounded-md border-gray-300 text-xs shadow-sm focus:border-indigo-500 focus:ring-indigo-500">
                <button id="review-ignore" type="submit" class="inline-flex items-center px-3 py-1.5 border border-gray-300 text-xs font-medium rounded-md text-gray-700 bg-white hover:bg-gray-50">Ignore</button>
            </form>
            <form hx-post="{% url 'research:item-disposition' result_set.project_id result_set.id item.id %}"
                  hx-target="#review-card" hx-swap="outerHTML" class="inline-flex">
                {% csrf_token %}
                <input type="hidden" name="disposition" value="u">
                <input type="hidden" name="lens" value="{{ querystring }}">
                <input type="hidden" name="next" value="review">
                <button id="review-unreviewed" type="submit" class="inline-flex items-center px-3 py-1.5 border border-gray-300 text-xs font-medium rounded-md text-gray-700 bg-white hover:bg-gray-50">Unreviewed</button>
            </form>
            {% if item.last_event_id %}
                <form hx-post="{% url 'research:event-undo' result_set.project_id result_set.id item.last_event_id %}"
                      hx-target="#review-card" hx-swap="outerHTML" class="inline-flex">
                    {% csrf_token %}
                    <input type="hidden" name="lens" value="{{ querystring }}">
                    <input type="hidden" name="next" value="review">
                    <button type="submit" class="inline-flex items-center px-3 py-1.5 border border-gray-300 text-xs font-medium rounded-md text-gray-700 bg-white hover:bg-gray-50">Undo</button>
                </form>
            {% endif %}
        </div>
        <div class="mt-4 flex items-center justify-between">
            <a id="review-prev"
               href="{% url 'research:set-review' result_set.project_id result_set.id %}{% if querystring %}?{{ querystring }}&{% else %}?{% endif %}before={{ cursor|urlencode }}"
               class="inline-flex items-center px-3 py-1.5 border border-gray-300 text-xs font-medium rounded-md text-gray-700 bg-white hover:bg-gray-50">Previous</a>
            <a id="review-next"
               href="{% url 'research:set-review' result_set.project_id result_set.id %}{% if querystring %}?{{ querystring }}&{% else %}?{% endif %}after={{ cursor|urlencode }}"
               class="inline-flex items-center px-3 py-1.5 border border-gray-300 text-xs font-medium rounded-md text-gray-700 bg-white hover:bg-gray-50">Next</a>
        </div>
    {% else %}
        <div class="text-center py-12">
            <h3 class="text-lg font-medium text-gray-900">No items left</h3>
            <p class="mt-2 text-gray-500">Every item in this view has been triaged.</p>
            <a href="{% url 'research:set-detail' result_set.project_id result_set.id %}{% if querystring %}?{{ querystring }}{% endif %}"
               class="mt-4 inline-flex items-center px-4 py-2 border border-gray-300 text-sm font-medium rounded-md text-gray-700 bg-white hover:bg-gray-50">Back to set</a>
        </div>
    {% endif %}
</div>
```

- [ ] **Step 10: Run the review view tests**

Run: `uv run --group test pytest tests/research/test_views.py::TestReviewView -q --no-cov`
Expected: PASS.

- [ ] **Step 11: Run the research suite**

Run: `uv run --group test pytest tests/research -q --no-cov`
Expected: PASS.

- [ ] **Step 12: Commit**

```bash
git add research/browse.py research/views.py research/urls.py templates/research/review.html templates/research/partials/review_item.html templates/research/set_detail.html tests/research/test_browse.py tests/research/test_views.py
git commit -m "feat: add keyboard review mode for research sets"
```

---

## Self-Review Checklist

- [ ] **Spec coverage:** Dispositions (`u`/`r`/`i`), one event per decision, `previous_disposition`, `last_event`, one-level undo, bulk actions over a lens, review mode with `j`/`k`/`r`/`i`/`u`, and owner scoping are all implemented. Bulk code application, code toggles in review, memos, ignore rules and the methods summary are explicitly deferred to Phase 1C/1D.
- [ ] **Placeholder scan:** no "TBD", "TODO", "similar to Task N", or unspecified error handling.
- [ ] **Type consistency:** `dispose_item`/`dispose_items`/`undo_disposition_event` signatures match their view call sites; `review_item` returns `(SetItem | None, str | None)` in both Task 1's `filter_items` note and Task 3's browse helper; URL names match the templates.
- [ ] **Review Focus:** each of the seven lines has a named test in Tasks 1–3.
