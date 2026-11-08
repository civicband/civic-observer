"""CSV and zip exports for research sets and their codebooks."""

from __future__ import annotations

import csv
import io
import zipfile
from collections import defaultdict
from collections.abc import Iterable, Iterator
from typing import Any

from django.db.models import Count

from meetings.models import MeetingPage

from .browse import filter_items
from .lens import Lens
from .methods import build_methods
from .models import Coding, Memo, Project, ResultSet

COLUMNS = [
    "page_id",
    "civic_band_url",
    "municipality",
    "state",
    "meeting_name",
    "meeting_date",
    "document_type",
    "page_number",
    "disposition",
    "ignore_reason",
    "codes",
    "memo",
    "snippet",
]

CODEBOOK_COLUMNS = [
    "code",
    "parent",
    "definition",
    "apply_when",
    "do_not_apply_when",
    "coding_count",
]

BATCH_SIZE = 1000
SNIPPET_LENGTH = 500


def _batches(values: Iterable[Any], size: int) -> Iterator[list[Any]]:
    batch: list[Any] = []
    for value in values:
        batch.append(value)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def _civic_band_url(page: MeetingPage) -> str:
    return (
        f"https://{page.municipality_subdomain}.civic.band"
        f"/meetings/{page.civic_band_table_name}/{page.id}"
    )


def export_rows(
    project: Project, result_set: ResultSet, lens: Lens
) -> Iterator[dict[str, Any]]:
    items = (
        filter_items(result_set, lens)
        .select_related("last_event")
        .order_by("id")
        .iterator(chunk_size=BATCH_SIZE)
    )

    for batch in _batches(items, BATCH_SIZE):
        page_ids = [item.page_id for item in batch]
        pages = MeetingPage.objects.in_bulk(page_ids)

        codes: dict[str, set[str]] = defaultdict(set)
        for page_id, name in Coding.objects.filter(
            project=project, page_id__in=page_ids
        ).values_list("page_id", "code__name"):
            codes[page_id].add(name)

        memos: dict[str, str] = {}
        for memo in (
            Memo.objects.filter(project=project, page_id__in=page_ids)
            .order_by("page_id", "-created")
            .only("page_id", "body")
        ):
            if memo.page_id is not None:
                memos.setdefault(memo.page_id, memo.body)

        for item in batch:
            page = pages.get(item.page_id)
            yield {
                "page_id": item.page_id,
                "civic_band_url": _civic_band_url(page) if page else "",
                "municipality": page.municipality_name if page else "",
                "state": item.state,
                "meeting_name": page.meeting_name if page else "",
                "meeting_date": (
                    item.meeting_date.isoformat() if item.meeting_date else ""
                ),
                "document_type": item.document_type,
                "page_number": page.page_number if page else "",
                "disposition": item.get_disposition_display(),
                "ignore_reason": item.ignore_reason,
                "codes": ";".join(sorted(codes.get(item.page_id, ()))),
                "memo": memos.get(item.page_id, ""),
                "snippet": (page.text if page else "")[:SNIPPET_LENGTH],
            }


def export_csv(project: Project, result_set: ResultSet, lens: Lens) -> Iterator[str]:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(COLUMNS)
    yield buffer.getvalue()

    for row in export_rows(project, result_set, lens):
        buffer.seek(0)
        buffer.truncate(0)
        writer.writerow([row[column] for column in COLUMNS])
        yield buffer.getvalue()


def export_codebook_csv(project: Project) -> Iterator[str]:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(CODEBOOK_COLUMNS)
    yield buffer.getvalue()

    codes = (
        project.codes.filter(is_archived=False)
        .select_related("parent")
        .annotate(coding_count=Count("codings"))
        .order_by("name")
    )
    for code in codes:
        buffer.seek(0)
        buffer.truncate(0)
        writer.writerow(
            [
                code.name,
                code.parent.name if code.parent else "",
                code.definition,
                code.inclusion,
                code.exclusion,
                code.coding_count,
            ]
        )
        yield buffer.getvalue()


def export_zip(project: Project, result_set: ResultSet, lens: Lens) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("export.csv", "".join(export_csv(project, result_set, lens)))
        archive.writestr("codebook.csv", "".join(export_codebook_csv(project)))
        archive.writestr("methods.md", build_methods(project))
    return buffer.getvalue()
