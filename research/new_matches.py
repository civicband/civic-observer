"""Explicitly re-run a set's frozen query for pages changed since the last check."""

from __future__ import annotations

import time

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

from meetings.models import MeetingPage
from searches import search_backends
from searches.queryspec import QuerySpec, build_where

from .models import AuditEvent, ResultSet, SetItem
from .rules import apply_active_rules_for_set

PAGE_SIZE = 1000


def _cutoff(result_set: ResultSet):
    return (
        result_set.last_checked_at or result_set.materialized_at or result_set.created
    )


def _spec(result_set: ResultSet, cutoff) -> QuerySpec:
    return QuerySpec.from_canonical(result_set.query or {}).with_modified_after(cutoff)


def count_new_matches(result_set: ResultSet) -> int:
    cutoff = _cutoff(result_set)
    spec = _spec(result_set, cutoff)
    return search_backends.get_search_backend().count_matches(
        spec, settings.RESEARCH_INLINE_SET_MAX_ITEMS + 1
    )


def check_for_new_matches(result_set: ResultSet, *, actor=None) -> AuditEvent:
    if result_set.origin != ResultSet.Origin.QUERY:
        raise ValueError("Only query-origin sets can check for new matches")
    checked_at = timezone.now()
    cutoff = _cutoff(result_set)
    spec = _spec(result_set, cutoff)
    started = time.monotonic()
    with transaction.atomic():
        event = AuditEvent.objects.create(
            project=result_set.project,
            actor=actor,
            result_set=result_set,
            verb="set.new_matches",
            payload={
                "cutoff": cutoff.isoformat(),
                "checked_at": checked_at.isoformat(),
                "added": 0,
                "ignored_by_rule": {},
            },
        )
        if settings.RESEARCH_USE_SQL_MATERIALIZATION:
            added = _insert_sql(result_set, spec, event)
        else:
            added = _insert_orm(result_set, spec, event)
        ignored = apply_active_rules_for_set(result_set, event=event, only_new=True)
        result_set.item_count = result_set.items.count()
        result_set.last_checked_at = checked_at
        result_set.save(update_fields=["item_count", "last_checked_at", "modified"])
        event.payload["added"] = added
        event.payload["ignored_by_rule"] = ignored
        event.payload["duration_ms"] = int((time.monotonic() - started) * 1000)
        event.save(update_fields=["payload"])
    return event


def create_new_matches(result_set: ResultSet, *, actor=None) -> ResultSet:
    if result_set.origin != ResultSet.Origin.QUERY:
        raise ValueError("Only query-origin sets can check for new matches")
    if count_new_matches(result_set) > settings.RESEARCH_INLINE_SET_MAX_ITEMS:
        enqueue_new_matches(
            result_set.id, actor_id=actor.id if actor is not None else None
        )
        return result_set
    check_for_new_matches(result_set, actor=actor)
    return result_set


def enqueue_new_matches(result_set_id, *, actor_id=None) -> None:
    import django_rq

    from .tasks import check_new_matches_task

    queue = django_rq.get_queue(settings.RESEARCH_QUEUE)
    queue.enqueue(
        check_new_matches_task,
        result_set_id,
        actor_id=actor_id,
        job_timeout=settings.RESEARCH_JOB_TIMEOUT,
    )


def _insert_sql(result_set: ResultSet, spec: QuerySpec, event: AuditEvent) -> int:
    where_sql, params = build_where(spec)
    # Gate A (2026-11-08): the installed pg_search rejects the qualified
    # paradedb.score(mp.id) inside INSERT ... SELECT with ON CONFLICT; the
    # unqualified form works. A facet-only spec has no BM25 predicate to score.
    rank_sql = "paradedb.score(id)" if (spec.text or spec.meeting_name) else "NULL"
    with connection.cursor() as cur:
        cur.execute(
            "SELECT set_config('statement_timeout', %s, true)",
            [str(settings.RESEARCH_STATEMENT_TIMEOUT_MS)],
        )
        cur.execute(
            f"""
            INSERT INTO research_setitem
                (result_set_id, page_id, rank, meeting_date, municipality_id,
                 state, document_type, disposition, previous_disposition,
                 ignore_reason, last_event_id, added_via, added_at)
            SELECT %s, mp.id, {rank_sql}, mp.meeting_date, mp.municipality_id,
                   mp.state, mp.document_type, 'u', '', '', %s, %s, now()
            FROM meetings_meetingpage mp
            WHERE {where_sql}
            ON CONFLICT (result_set_id, page_id) DO NOTHING
            """,
            [result_set.id, event.id, SetItem.AddedVia.NEW_MATCHES, *params],
        )
        return cur.rowcount


def _insert_orm(result_set: ResultSet, spec: QuerySpec, event: AuditEvent) -> int:
    backend = search_backends.get_search_backend()
    before = result_set.items.count()
    offset = 0
    while True:
        results, _ = backend.search(
            query_text=spec.text,
            municipalities=list(spec.municipality_ids),
            states=list(spec.states),
            date_from=spec.date_from,
            date_to=spec.date_to,
            document_type=spec.document_type,
            meeting_name_query=spec.meeting_name,
            modified_after=spec.modified_after,
            limit=PAGE_SIZE,
            offset=offset,
        )
        if not results:
            break
        ids = [row["id"] for row in results]
        pages = MeetingPage.objects.in_bulk(ids)
        SetItem.objects.bulk_create(
            [
                SetItem(
                    result_set=result_set,
                    page=pages[page_id],
                    meeting_date=pages[page_id].meeting_date,
                    municipality_id=pages[page_id].municipality_id,
                    state=pages[page_id].state,
                    document_type=pages[page_id].document_type,
                    last_event=event,
                    added_via=SetItem.AddedVia.NEW_MATCHES,
                )
                for page_id in ids
                if page_id in pages
            ],
            ignore_conflicts=True,
        )
        if len(results) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return result_set.items.count() - before
