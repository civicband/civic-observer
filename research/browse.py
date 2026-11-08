"""Browse and facet a materialized research set without scanning the corpus."""

from __future__ import annotations

from dataclasses import replace
from datetime import date

from django.db.models import Count, Exists, F, OuterRef, Q
from django.db.models.functions import ExtractYear

from meetings.models import MeetingPage
from searches import search_backends
from searches.queryspec import QuerySpec

from .lens import Lens
from .models import Coding, SetItem

# Ids fetched per backend round-trip when resolving a text sub-search.
PAGE_SIZE = 1000


def filter_items(result_set, lens: Lens):
    items = SetItem.objects.filter(result_set=result_set)

    if lens.dispositions:
        items = items.filter(disposition__in=lens.dispositions)
    if lens.states:
        items = items.filter(state__in=lens.states)
    if lens.municipality_ids:
        items = items.filter(municipality_id__in=lens.municipality_ids)
    if lens.date_from:
        items = items.filter(meeting_date__gte=lens.date_from)
    if lens.date_to:
        items = items.filter(meeting_date__lte=lens.date_to)
    if lens.document_type and lens.document_type != "all":
        items = items.filter(document_type=lens.document_type)
    if lens.added_via:
        items = items.filter(added_via__in=lens.added_via)

    codings = Coding.objects.filter(project=result_set.project, page=OuterRef("page"))
    for code_id in lens.codes_all:
        items = items.filter(Exists(codings.filter(code_id=code_id)))
    if lens.codes_any:
        items = items.filter(Exists(codings.filter(code_id__in=lens.codes_any)))
    if lens.codes_none:
        items = items.filter(~Exists(codings.filter(code_id__in=lens.codes_none)))
    if lens.uncoded:
        items = items.filter(~Exists(codings))

    if lens.text:
        items = items.filter(page_id__in=_text_match_page_ids(result_set, lens.text))

    return items


def _text_match_page_ids(result_set, text: str) -> list[str]:
    spec = QuerySpec.from_canonical(result_set.query or {})
    combined = f"({spec.text}) AND ({text})" if spec.text else text
    spec = replace(spec, text=combined)

    backend = search_backends.get_search_backend()
    page_ids: list[str] = []
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
            break
        page_ids.extend(row["id"] for row in results)
        if len(results) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    return page_ids


def _ordered(items, order: str):
    if order == "date_asc":
        return items.order_by(F("meeting_date").asc(nulls_last=True), "page_id")
    if order == "date_desc":
        return items.order_by(F("meeting_date").desc(nulls_last=True), "page_id")
    return items.order_by(F("rank").desc(nulls_last=True), "page_id")


def _cursor_value(item: SetItem, order: str) -> str:
    if order.startswith("date"):
        return item.meeting_date.isoformat() if item.meeting_date else ""
    return "" if item.rank is None else str(item.rank)


def _encode_cursor(item: SetItem, order: str) -> str:
    return f"{_cursor_value(item, order)}|{item.page_id}"


def _apply_cursor(items, after: str, order: str):
    first, separator, page_id = after.partition("|")
    if not separator:
        raise ValueError(f"Invalid cursor: {after!r}")

    descending = order != "date_asc"

    if order.startswith("date"):
        if first:
            date_value = date.fromisoformat(first)
            if descending:
                condition = (
                    Q(meeting_date__lt=date_value)
                    | (Q(meeting_date=date_value) & Q(page__gt=page_id))
                    | Q(meeting_date__isnull=True)
                )
            else:
                condition = (
                    Q(meeting_date__gt=date_value)
                    | (Q(meeting_date=date_value) & Q(page__gt=page_id))
                    | Q(meeting_date__isnull=True)
                )
        else:
            condition = Q(meeting_date__isnull=True, page__gt=page_id)
    elif first:
        rank_value = float(first)
        condition = (
            Q(rank__lt=rank_value)
            | (Q(rank=rank_value) & Q(page__gt=page_id))
            | Q(rank__isnull=True)
        )
    else:
        condition = Q(rank__isnull=True, page__gt=page_id)

    return items.filter(condition)


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

    items = filter_items(result_set, lens).select_related("last_event")
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


def browse_set(
    result_set, lens: Lens, *, limit: int = 50, after: str | None = None
) -> tuple[list[SetItem], str | None]:
    items = filter_items(result_set, lens).select_related("last_event")
    items = _ordered(items, lens.order)
    if after:
        items = _apply_cursor(items, after, lens.order)

    rows = list(items[: limit + 1])
    has_next = len(rows) > limit
    pages = MeetingPage.objects.in_bulk([item.page_id for item in rows])
    rows = rows[:limit]
    for item in rows:
        item.page_obj = pages.get(item.page_id)
    next_cursor = _encode_cursor(rows[-1], lens.order) if has_next and rows else None
    return rows, next_cursor


def set_facets(result_set, lens: Lens) -> dict:
    items = filter_items(result_set, lens)

    dispositions = list(
        items.values("disposition")
        .annotate(count=Count("id"))
        .order_by("-count", "disposition")
    )
    states = list(
        items.exclude(state="")
        .values("state")
        .annotate(count=Count("id"))
        .order_by("-count", "state")
    )
    municipalities = list(
        items.exclude(municipality_id__isnull=True)
        .values("municipality_id")
        .annotate(count=Count("id"))
        .order_by("-count", "municipality_id")[:20]
    )
    years = list(
        items.filter(meeting_date__isnull=False)
        .annotate(year=ExtractYear("meeting_date"))
        .values("year")
        .annotate(count=Count("id"))
        .order_by("year")
    )
    document_types = list(
        items.exclude(document_type="")
        .values("document_type")
        .annotate(count=Count("id"))
        .order_by("-count", "document_type")
    )

    return {
        "disposition": dispositions,
        "states": states,
        "municipalities": municipalities,
        "years": years,
        "document_types": document_types,
    }
