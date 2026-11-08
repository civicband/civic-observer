"""Freeze a lens over a parent set into a child result set."""

from __future__ import annotations

import hashlib
import json

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .audit import record_event
from .browse import filter_items
from .lens import Lens
from .models import ResultSet, SetItem
from .rules import apply_active_rules_for_set

BATCH = 1000


def _child_query(parent: ResultSet, lens: Lens, copy_dispositions: bool) -> dict:
    return {
        "parent": str(parent.id),
        "lens": lens.canonical(),
        "copy_dispositions": copy_dispositions,
    }


def _query_hash(query: dict) -> str:
    return hashlib.sha256(
        json.dumps(query, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def freeze_lens(
    parent: ResultSet,
    name: str,
    lens: Lens,
    *,
    copy_dispositions: bool = True,
) -> ResultSet:
    query = _child_query(parent, lens, copy_dispositions)
    child = ResultSet.objects.create(
        project=parent.project,
        parent=parent,
        name=name,
        origin=ResultSet.Origin.LENS,
        query=query,
        query_hash=_query_hash(query),
        status=ResultSet.Status.PENDING,
    )
    count = filter_items(parent, lens).count()
    if count > settings.RESEARCH_SET_MAX_ITEMS:
        child.status = ResultSet.Status.TOO_LARGE
        child.item_count = count
        child.error = (
            f"This lens matches at least {count} pages, more than the "
            f"{settings.RESEARCH_SET_MAX_ITEMS:,}-page set limit."
        )
        child.save(update_fields=["status", "item_count", "error", "modified"])
        return child
    if count <= settings.RESEARCH_INLINE_SET_MAX_ITEMS:
        return materialize_lens_set(child.id)
    enqueue_lens_materialization(child.id)
    return child


def enqueue_lens_materialization(result_set_id) -> None:
    import django_rq

    from .tasks import materialize_lens_set_task

    queue = django_rq.get_queue(settings.RESEARCH_QUEUE)
    queue.enqueue(
        materialize_lens_set_task,
        result_set_id,
        job_timeout=settings.RESEARCH_JOB_TIMEOUT,
    )


def materialize_lens_set(result_set_id) -> ResultSet:
    child = ResultSet.objects.get(pk=result_set_id)
    lens = Lens.from_canonical((child.query or {}).get("lens", {}))
    parent_id = (child.query or {}).get("parent")
    if not parent_id:
        raise ValueError("Lens set is missing its parent result set")
    parent = ResultSet.objects.get(pk=str(parent_id))
    copy_dispositions = bool((child.query or {}).get("copy_dispositions", True))
    now = timezone.now()
    child.status = ResultSet.Status.MATERIALIZING
    child.error = ""
    child.save(update_fields=["status", "error", "modified"])
    try:
        with transaction.atomic():
            source = filter_items(parent, lens).values(
                "page_id",
                "rank",
                "meeting_date",
                "municipality_id",
                "state",
                "document_type",
                "disposition",
                "ignore_reason",
            )
            batch: list[SetItem] = []
            for row in source.iterator(chunk_size=BATCH):
                batch.append(
                    SetItem(
                        result_set=child,
                        page_id=row["page_id"],
                        rank=row["rank"],
                        meeting_date=row["meeting_date"],
                        municipality_id=row["municipality_id"],
                        state=row["state"],
                        document_type=row["document_type"],
                        disposition=row["disposition"]
                        if copy_dispositions
                        else SetItem.Disposition.UNREVIEWED,
                        previous_disposition="",
                        disposition_by_rule=None,
                        ignore_reason=row["ignore_reason"] if copy_dispositions else "",
                        added_via=SetItem.AddedVia.LENS,
                        added_at=now,
                    )
                )
                if len(batch) >= BATCH:
                    SetItem.objects.bulk_create(
                        batch, ignore_conflicts=True, batch_size=BATCH
                    )
                    batch = []
            if batch:
                SetItem.objects.bulk_create(
                    batch, ignore_conflicts=True, batch_size=BATCH
                )

            event = record_event(
                child.project,
                "set.derived",
                result_set=child,
                payload={
                    "parent": str(parent.id),
                    "lens": lens.canonical(),
                    "item_count": 0,
                    "dispositions_copied": copy_dispositions,
                    "ignored_by_rule": {},
                },
            )
            ignored = apply_active_rules_for_set(child, event=event)
            child.item_count = child.items.count()
            child.materialized_at = now
            child.status = ResultSet.Status.READY
            child.save(
                update_fields=["item_count", "materialized_at", "status", "modified"]
            )
            event.payload["item_count"] = child.item_count
            event.payload["ignored_by_rule"] = ignored
            event.save(update_fields=["payload"])
    except Exception as exc:
        child.status = ResultSet.Status.FAILED
        child.error = str(exc)
        child.save(update_fields=["status", "error", "modified"])
        raise
    return child
