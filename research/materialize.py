"""Materialize a frozen snapshot of a search into ``ResultSet`` rows."""

from __future__ import annotations

import time

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

from meetings.models import MeetingPage
from searches import search_backends
from searches.queryspec import QuerySpec, build_where

from .models import AuditEvent, ResultSet, SetItem

# Pages fetched per backend round-trip when hydrating through the ORM.
PAGE_SIZE = 1000


def _spec_from_set(result_set: ResultSet) -> QuerySpec:
    """Rebuild the immutable query spec stored on a set."""
    return QuerySpec.from_canonical(result_set.query or {})


def create_result_set(
    project,
    name,
    spec: QuerySpec,
    origin=ResultSet.Origin.QUERY,
    parent=None,
) -> ResultSet:
    """Create a set and either materialize it inline or queue the job."""
    result_set = ResultSet.objects.create(
        project=project,
        name=name,
        origin=origin,
        parent=parent,
        query=spec.canonical(),
        query_hash=spec.query_hash(),
        status=ResultSet.Status.PENDING,
    )

    max_items = settings.RESEARCH_SET_MAX_ITEMS
    try:
        count = search_backends.get_search_backend().count_matches(spec, max_items + 1)
    except Exception as exc:
        result_set.status = ResultSet.Status.FAILED
        result_set.error = str(exc)
        result_set.save(update_fields=["status", "error", "modified"])
        return result_set

    if count > max_items:
        result_set.status = ResultSet.Status.TOO_LARGE
        result_set.item_count = count
        result_set.error = (
            f"This query matches at least {count} pages, more than the "
            f"{max_items:,}-page set limit. Narrow the query by state, body, "
            "or date."
        )
        result_set.save(update_fields=["status", "item_count", "error", "modified"])
        return result_set

    if count <= settings.RESEARCH_INLINE_SET_MAX_ITEMS:
        try:
            return materialize_result_set(result_set.id)
        except Exception:
            # materialize_result_set records FAILED before re-raising.
            result_set.refresh_from_db()
            return result_set

    try:
        enqueue_materialization(result_set.id)
    except Exception as exc:
        result_set.status = ResultSet.Status.FAILED
        result_set.error = str(exc)
        result_set.save(update_fields=["status", "error", "modified"])
    return result_set


def enqueue_materialization(result_set_id) -> None:
    """Queue materialization on the dedicated research queue."""
    import django_rq

    from .tasks import materialize_result_set_task

    queue = django_rq.get_queue(settings.RESEARCH_QUEUE)
    queue.enqueue(
        materialize_result_set_task,
        result_set_id,
        job_timeout=settings.RESEARCH_JOB_TIMEOUT,
    )


def materialize_result_set(result_set_id) -> ResultSet:
    """Insert every match for the set's query, idempotently."""
    result_set = ResultSet.objects.get(pk=result_set_id)
    started = time.monotonic()

    result_set.status = ResultSet.Status.MATERIALIZING
    result_set.error = ""
    result_set.save(update_fields=["status", "error", "modified"])

    # One timestamp is both the snapshot cutoff and the items' added_at.
    now = timezone.now()

    try:
        with transaction.atomic():
            if settings.RESEARCH_USE_SQL_MATERIALIZATION:
                _insert_items_sql(result_set, now)
            else:
                _insert_items_orm(result_set)

            result_set.item_count = result_set.items.count()
            result_set.materialized_at = now
            result_set.status = ResultSet.Status.READY
            result_set.save(
                update_fields=[
                    "item_count",
                    "materialized_at",
                    "status",
                    "modified",
                ]
            )
            AuditEvent.objects.create(
                project=result_set.project,
                result_set=result_set,
                verb="set.materialized",
                payload={
                    "query": result_set.query,
                    "item_count": result_set.item_count,
                    "duration_ms": int((time.monotonic() - started) * 1000),
                    "ignored_by_rule": {},
                },
            )
    except Exception as exc:
        result_set.status = ResultSet.Status.FAILED
        result_set.error = str(exc)
        result_set.save(update_fields=["status", "error", "modified"])
        raise

    return result_set


def _insert_items_sql(result_set: ResultSet, now) -> None:
    """Run one INSERT ... SELECT inside Postgres, never paging ids through Python."""
    spec = _spec_from_set(result_set)
    where_sql, where_params = build_where(spec)

    # paradedb.score() needs a BM25 predicate in the same query; a purely
    # facet-filtered set has no score to record.
    rank_sql = "paradedb.score(mp.id)" if (spec.text or spec.meeting_name) else "NULL"

    sql = f"""
        INSERT INTO research_setitem
            (result_set_id, page_id, rank, meeting_date, municipality_id,
             state, document_type, disposition, previous_disposition,
             ignore_reason, added_via, added_at)
        SELECT %s, mp.id, {rank_sql}, mp.meeting_date, mp.municipality_id,
               mp.state, mp.document_type, %s, '', '', %s, %s
        FROM meetings_meetingpage mp
        WHERE {where_sql}
        ON CONFLICT (result_set_id, page_id) DO NOTHING
    """
    params = [
        result_set.id,
        SetItem.Disposition.UNREVIEWED,
        result_set.origin,
        now,
        *where_params,
    ]

    with connection.cursor() as cur:
        cur.execute(
            "SELECT set_config('statement_timeout', %s, true)",
            [str(settings.RESEARCH_STATEMENT_TIMEOUT_MS)],
        )
        cur.execute(sql, params)


def _insert_items_orm(result_set: ResultSet) -> None:
    """Page the search backend and bulk-insert the hydrated pages.

    This is the test/fallback path: it is slower and lets ids pass through
    Python, but needs no ParadeDB-specific INSERT ... SELECT.
    """
    spec = _spec_from_set(result_set)
    backend = search_backends.get_search_backend()
    offset = 0

    while True:
        results, _total = backend.search(
            query_text=spec.text,
            municipalities=list(spec.municipality_ids),
            states=list(spec.states),
            date_from=spec.date_from,
            date_to=spec.date_to,
            document_type=spec.document_type,
            meeting_name_query=spec.meeting_name,
            limit=PAGE_SIZE,
            offset=offset,
            modified_after=spec.modified_after,
        )
        if not results:
            return

        page_ids = [row["id"] for row in results]
        pages = MeetingPage.objects.in_bulk(page_ids)
        items = []
        for page_id in page_ids:
            page = pages.get(page_id)
            if page is None:
                continue
            items.append(
                SetItem(
                    result_set=result_set,
                    page=page,
                    rank=None,
                    meeting_date=page.meeting_date,
                    municipality_id=page.municipality_id,
                    state=page.state,
                    document_type=page.document_type,
                    added_via=result_set.origin,
                )
            )
        SetItem.objects.bulk_create(items, ignore_conflicts=True)

        if len(results) < PAGE_SIZE:
            return
        offset += PAGE_SIZE
