"""Disposition changes and one-level undo for research set items."""

from __future__ import annotations

from django.db import transaction
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
    if disposition != SetItem.Disposition.IGNORED:
        reason = ""
    if (
        item.disposition == disposition
        and item.ignore_reason == reason
        and item.disposition_by_rule is None
    ):
        return None

    previous = item.disposition
    with transaction.atomic():
        event = record_event(
            item.result_set.project,
            "items.disposed",
            actor=actor,
            result_set=item.result_set,
            payload={
                "item_ids": [item.id],
                "from": {previous: 1},
                "to": disposition,
                "reason": reason,
                "count": 1,
            },
        )
        item.previous_disposition = previous
        item.disposition = disposition
        item.ignore_reason = reason
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
    if disposition != SetItem.Disposition.IGNORED:
        reason = ""
    items: QuerySet[SetItem] = filter_items(result_set, lens)
    changed = items.exclude(disposition=disposition, ignore_reason=reason)
    counts = {
        row["disposition"]: row["count"]
        for row in changed.values("disposition").annotate(count=Count("id"))
    }
    count = sum(counts.values())
    if count == 0:
        return None, 0

    with transaction.atomic():
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
        changed.update(
            previous_disposition=F("disposition"),
            disposition=disposition,
            ignore_reason=reason,
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

    with transaction.atomic():
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
