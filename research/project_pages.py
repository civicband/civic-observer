"""Browse distinct pages across every set in a project through a Lens."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from django.db.models import Case, Count, F, IntegerField, Min, OuterRef, Q, When

from meetings.models import MeetingPage
from searches import search_backends

from .lens import Lens
from .models import Coding, Project, ResultSet, SetItem

READY = ResultSet.Status.READY

# Derived disposition precedence: Real>Ign but we want Relevant to win, so the
# minimum numeric rank is the most important. Lower = stronger.
DISP_RANK: dict[str, int] = {"r": 1, "i": 2, "u": 3}
RANK_DISP: dict[int, str] = {value: key for key, value in DISP_RANK.items()}
TEXT_BATCH = 1000


@dataclass
class ProjectPageRow:
    page: MeetingPage
    disposition: str
    meeting_date: date | None
    sets: list[dict] = field(default_factory=list)
    codes: list[str] = field(default_factory=list)


def _disposition_rank(disposition: str, default: int = 3) -> int:
    return DISP_RANK.get(disposition, default)


def _base_items(project: Project):
    return SetItem.objects.filter(
        result_set__project=project, result_set__status=READY
    )


def _text_page_ids(project: Project, text: str) -> list[str]:
    """Pages in the project whose page text matches ``text`` via the backend."""
    backend = search_backends.get_search_backend()
    page_ids: list[str] = []
    offset = 0
    while True:
        results, _total = backend.search(
            query_text=text, limit=TEXT_BATCH, offset=offset
        )
        if not results:
            break
        page_ids.extend(row["id"] for row in results)
        if len(results) < TEXT_BATCH:
            break
        offset += TEXT_BATCH
    return page_ids


def project_page_queryset(project: Project, lens: Lens):
    """Return ``(page_id, rank, meeting_date)`` rows grouped by distinct page.

    ``rank`` is the derived disposition rank (1 relevant, 2 ignored, 3
    unreviewed). Item-level filters are applied before grouping; the
    disposition filter is applied to the derived rank afterwards.
    """
    grouped = (
        _filter_items_for_lens(project, lens)
        .order_by()
        .values("page_id")
        .annotate(
            rank=_derived_disposition_rank(),
            meeting_date=Min("meeting_date"),
        )
    )

    if lens.dispositions:
        ranks = [_disposition_rank(value) for value in lens.dispositions]
        grouped = grouped.filter(rank__in=ranks)
    return grouped


def _filter_items_for_lens(project: Project, lens: Lens):
    """Apply every item-level Lens condition (all but dispositions)."""
    items = _base_items(project)

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

    codings = Coding.objects.filter(project=project, page=OuterRef("page"))
    if lens.codes_all:
        for code_id in lens.codes_all:
            items = items.filter(
                page_id__in=codings.filter(code_id=code_id).values("page_id")
            )
    if lens.codes_any:
        items = items.filter(
            page_id__in=codings.filter(code_id__in=lens.codes_any).values("page_id")
        )
    if lens.codes_none:
        items = items.exclude(
            page_id__in=codings.filter(code_id__in=lens.codes_none).values("page_id")
        )
    if lens.uncoded:
        items = items.exclude(page_id__in=codings.values("page_id"))

    if lens.text:
        items = items.filter(page_id__in=_text_page_ids(project, lens.text))
    return items


def _ordered(grouped, order: str):
    if order == "date_asc":
        return grouped.order_by(F("meeting_date").asc(nulls_last=True), "page_id")
    return grouped.order_by(F("meeting_date").desc(nulls_last=True), "page_id")


def _cursor_value(row, order: str) -> str:
    value = row["meeting_date"]
    return value.isoformat() if value else ""


def _encode_cursor(row, order: str) -> str:
    return f"{_cursor_value(row, order)}|{row['page_id']}"


def _apply_cursor(grouped, after: str, order: str):
    first, separator, page_id = after.partition("|")
    if not separator:
        raise ValueError(f"Invalid cursor: {after!r}")
    ascending = order == "date_asc"
    if first:
        date_value = date.fromisoformat(first)
        if ascending:
            condition = Q(meeting_date__gt=date_value) | (
                Q(meeting_date=date_value) & Q(page_id__gt=page_id)
            )
        else:
            condition = Q(meeting_date__lt=date_value) | (
                Q(meeting_date=date_value) & Q(page_id__gt=page_id)
            )
        condition |= Q(meeting_date__isnull=True)
        return grouped.filter(condition)
    if ascending:
        condition = Q(meeting_date__isnull=True, page_id__gt=page_id) | Q(
            meeting_date__isnull=False
        )
    else:
        condition = Q(meeting_date__isnull=True, page_id__gt=page_id)
    return grouped.filter(condition)


def browse_project_pages(
    project: Project, lens: Lens, *, limit: int = 50, after: str | None = None
) -> tuple[list[ProjectPageRow], str | None]:
    grouped = _ordered(project_page_queryset(project, lens), lens.order)
    if after:
        grouped = _apply_cursor(grouped, after, lens.order)

    rows = list(grouped[: limit + 1])
    has_next = len(rows) > limit
    rows = rows[:limit]
    if not rows:
        return [], None

    page_ids = [row["page_id"] for row in rows]
    pages = MeetingPage.objects.in_bulk(page_ids)

    memberships: dict[str, list[dict]] = {page_id: [] for page_id in page_ids}
    for page_id, set_id, set_name, disposition in (
        _base_items(project)
        .filter(page_id__in=page_ids)
        .values_list("page_id", "result_set_id", "result_set__name", "disposition")
    ):
        memberships.setdefault(page_id, []).append(
            {
                "id": set_id,
                "name": set_name,
                "disposition": disposition,
            }
        )

    codes: dict[str, list[str]] = {page_id: [] for page_id in page_ids}
    for page_id, name in (
        Coding.objects.filter(project=project, page_id__in=page_ids)
        .order_by("code__name")
        .values_list("page_id", "code__name")
    ):
        codes.setdefault(page_id, []).append(name)

    result: list[ProjectPageRow] = []
    for row in rows:
        page = pages.get(row["page_id"])
        if page is None:
            continue
        result.append(
            ProjectPageRow(
                page=page,
                disposition=RANK_DISP.get(row["rank"], SetItem.Disposition.UNREVIEWED),
                meeting_date=row["meeting_date"],
                sets=memberships.get(row["page_id"], []),
                codes=codes.get(row["page_id"], []),
            )
        )

    next_cursor = _encode_cursor(rows[-1], lens.order) if has_next and rows else None
    return result, next_cursor


def _derived_disposition_rank():
    return Min(
        Case(
            When(disposition=SetItem.Disposition.RELEVANT, then=1),
            When(disposition=SetItem.Disposition.IGNORED, then=2),
            default=3,
            output_field=IntegerField(),
        )
    )


def project_page_counts(project: Project) -> dict[str, int]:
    """Counts of distinct pages per derived disposition across the project.

    Filtering the grouped (one-row-per-page) queryset by its derived rank gives
    an exact per-disposition count in three cheap queries.
    """
    grouped = (
        _base_items(project)
        .order_by()
        .values("page_id")
        .annotate(rank=_derived_disposition_rank())
    )
    counts: dict[str, int] = {
        "r": grouped.filter(rank=1).count(),
        "i": grouped.filter(rank=2).count(),
        "u": grouped.filter(rank=3).count(),
    }
    counts["total"] = sum(counts.values())
    return counts


def project_page_facets(project: Project, lens: Lens) -> dict:
    """Facet counts over the derived page rows.

    Disposition counts are exact for the current Lens. The state /
    municipality / document-type counts are computed over the same filtered
    item pool (before disposition grouping) so they stay a single query each.
    """
    dispositions = []
    for rank in (1, 2, 3):
        count = project_page_queryset(project, lens).filter(rank=rank).count()
        dispositions.append({"disposition": RANK_DISP[rank], "count": count})

    items = _filter_items_for_lens(project, lens).order_by()
    states = list(
        items.exclude(state="")
        .values("state")
        .annotate(count=Count("page_id", distinct=True))
        .order_by("-count", "state")
    )
    municipalities = list(
        items.exclude(municipality_id__isnull=True)
        .values("municipality_id")
        .annotate(count=Count("page_id", distinct=True))
        .order_by("-count", "municipality_id")[:20]
    )
    document_types = list(
        items.exclude(document_type="")
        .values("document_type")
        .annotate(count=Count("page_id", distinct=True))
        .order_by("-count", "document_type")
    )
    return {
        "disposition": dispositions,
        "states": states,
        "municipalities": municipalities,
        "document_types": document_types,
    }


def freeze_project_lens(
    project: Project, name: str, lens: Lens, *, actor=None
) -> ResultSet:
    """Freeze a project-scoped Lens into a new project-level set.

    The new set holds one ``SetItem`` per distinct matching page, with the page's
    derived disposition preserved.
    """
    from django.conf import settings
    from django.db import transaction
    from django.utils import timezone

    from .audit import record_event

    result_set = ResultSet.objects.create(
        project=project,
        name=name,
        origin=ResultSet.Origin.LENS,
        query={"project_lens": lens.canonical()},
        status=ResultSet.Status.PENDING,
    )

    rows: list[SetItem] = []
    grouped = project_page_queryset(project, lens)
    now = timezone.now()
    for row in grouped.iterator(chunk_size=1000):
        rows.append(
            SetItem(
                result_set=result_set,
                page_id=row["page_id"],
                meeting_date=row["meeting_date"],
                disposition=RANK_DISP.get(row["rank"], SetItem.Disposition.UNREVIEWED),
                added_via=SetItem.AddedVia.LENS,
                added_at=now,
            )
        )

    if len(rows) > settings.RESEARCH_SET_MAX_ITEMS:
        result_set.status = ResultSet.Status.TOO_LARGE
        result_set.item_count = len(rows)
        result_set.error = (
            f"This Lens matches at least {len(rows)} pages, more than the "
            f"{settings.RESEARCH_SET_MAX_ITEMS:,}-page set limit."
        )
        result_set.save(update_fields=["status", "item_count", "error", "modified"])
        return result_set

    with transaction.atomic():
        SetItem.objects.bulk_create(rows, ignore_conflicts=True, batch_size=1000)
        result_set.item_count = len(rows)
        result_set.materialized_at = now
        result_set.status = ResultSet.Status.READY
        result_set.save(
            update_fields=["item_count", "materialized_at", "status", "modified"]
        )
        record_event(
            project,
            "set.derived",
            actor=actor,
            result_set=result_set,
            payload={
                "lens": lens.canonical(),
                "item_count": result_set.item_count,
                "dispositions_copied": True,
            },
        )
    return result_set


__all__ = [
    "ProjectPageRow",
    "browse_project_pages",
    "freeze_project_lens",
    "project_page_counts",
    "project_page_facets",
    "project_page_queryset",
]
