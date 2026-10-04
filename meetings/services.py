"""
Service module for backfilling meeting data from civic.band datasette API.
"""

import logging
from datetime import date
from typing import Any

import httpx
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from meetings.models import MeetingDocument, MeetingPage
from municipalities.models import Muni

logger = logging.getLogger(__name__)


def civic_band_table_url(subdomain: str, table_name: str) -> str:
    """Return the civic.band datasette JSON URL for a municipality table."""
    return f"https://{subdomain}.civic.band/meetings/{table_name}.json"


def civic_band_headers() -> dict[str, str]:
    """Return auth headers for civic.band requests (service secret if set)."""
    headers: dict[str, str] = {}
    service_secret = getattr(settings, "CORKBOARD_SERVICE_SECRET", "")
    if service_secret:
        headers["X-Service-Secret"] = service_secret
    return headers


class BackfillError(Exception):
    """Exception raised when backfill operation fails."""

    pass


def backfill_municipality_meetings(muni: Muni, timeout: int = 60) -> dict[str, int]:
    """
    Backfill all meeting data (agendas and minutes) for a municipality from civic.band.

    Args:
        muni: Municipality instance to backfill meetings for
        timeout: HTTP request timeout in seconds

    Returns:
        Dictionary with statistics:
        {
            'documents_created': int,
            'documents_updated': int,
            'pages_created': int,
            'pages_updated': int,
            'errors': int
        }

    Raises:
        BackfillError: If the backfill operation fails
    """
    logger.info(f"Starting backfill for {muni.subdomain}")

    stats = {
        "documents_created": 0,
        "documents_updated": 0,
        "pages_created": 0,
        "pages_updated": 0,
        "errors": 0,
    }

    try:
        # Backfill agendas
        agenda_stats, _ = _backfill_document_type(
            muni, "agendas", "agenda", timeout=timeout
        )
        for key in stats:
            stats[key] += agenda_stats[key]

        # Backfill minutes
        minutes_stats, _ = _backfill_document_type(
            muni, "minutes", "minutes", timeout=timeout
        )
        for key in stats:
            stats[key] += minutes_stats[key]

        # Update last_indexed timestamp on successful completion
        muni.last_indexed = timezone.now()
        muni.save(update_fields=["last_indexed"])

        logger.info(f"Backfill completed for {muni.subdomain}: {stats}")
        return stats

    except Exception as e:
        logger.error(f"Backfill failed for {muni.subdomain}: {e}", exc_info=True)
        raise BackfillError(f"Failed to backfill {muni.subdomain}: {e}") from e


def _backfill_document_type(
    muni: Muni,
    table_name: str,
    document_type: str,
    start_cursor: str | None = None,
    max_pages: int | None = None,
    date_range: tuple[date, date] | None = None,
    timeout: int = 60,
) -> tuple[dict[str, int], str | None]:
    """
    Backfill meeting documents with support for batching and date filtering.

    This function can operate in three modes:
    1. Full backfill: Fetch all historical data (no date_range, no start_cursor)
    2. Batched backfill: Fetch N pages at a time (max_pages set, returns cursor)
    3. Incremental backfill: Fetch specific date range (date_range set)

    Supports resuming from a cursor (start_cursor) for fault tolerance.

    Args:
        muni: Municipality instance
        table_name: Name of the datasette table ('agendas' or 'minutes')
        document_type: Type of document ('agenda' or 'minutes')
        start_cursor: Pagination cursor to resume from (optional)
        max_pages: Maximum number of API pages to fetch (optional, for batching)
        date_range: Tuple of (start_date, end_date) for incremental backfill (optional)
        timeout: HTTP request timeout in seconds

    Returns:
        Tuple of (stats dict, next_cursor or None)
    """
    stats = {
        "documents_created": 0,
        "documents_updated": 0,
        "pages_created": 0,
        "pages_updated": 0,
        "errors": 0,
    }

    base_url = civic_band_table_url(muni.subdomain, table_name)

    try:
        # Build headers with service secret for authentication
        headers = civic_band_headers()

        with httpx.Client(timeout=timeout, headers=headers) as client:
            # Build query parameters
            params: dict[str, int | str] = {"_size": 1000}

            # Add date filtering for incremental mode
            if date_range:
                start_date, end_date = date_range
                params["date__gte"] = start_date.isoformat()
                params["date__lte"] = end_date.isoformat()

            # Resume from cursor if provided
            if start_cursor:
                params["_next"] = start_cursor

            pages_fetched = 0
            next_cursor = None

            while True:
                logger.debug(f"Fetching {base_url} with params {params}")
                response = client.get(base_url, params=params)
                response.raise_for_status()

                try:
                    data = response.json()
                except ValueError as e:
                    raise BackfillError(
                        f"Non-JSON response fetching {table_name} "
                        f"for {muni.subdomain}: {e}"
                    ) from e

                # Get rows from the response
                rows = data.get("rows", [])

                # Process rows in batches
                _process_rows_batch(muni, rows, document_type, stats)

                pages_fetched += 1

                # Check if there's a next page using the cursor
                next_cursor = data.get("next")

                # Stop if: no more pages OR reached batch limit
                if not next_cursor or (max_pages and pages_fetched >= max_pages):
                    break

                # Use the next cursor for pagination
                params = {"_size": 1000, "_next": next_cursor}

                # Preserve date filters if they were set
                if date_range:
                    params["date__gte"] = start_date.isoformat()
                    params["date__lte"] = end_date.isoformat()

    except httpx.HTTPError as e:
        logger.error(
            f"HTTP error fetching {table_name} for {muni.subdomain}: {e}",
            exc_info=True,
        )
        raise BackfillError(
            f"HTTP error fetching {table_name} for {muni.subdomain}: {e}"
        ) from e

    return stats, next_cursor


# Columns refreshed when a page row already exists. `created` is intentionally
# omitted so the original ingestion time survives re-ingestion.
PAGE_UPSERT_UPDATE_FIELDS = [
    "document",
    "page_number",
    "text",
    "page_image",
    "municipality",
    "municipality_subdomain",
    "municipality_name",
    "state",
    "meeting_name",
    "meeting_date",
    "document_type",
    "modified",
]


def group_rows_by_document(
    rows: list[dict[str, Any]], stats: dict[str, int]
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """
    Group API rows by (meeting_name, date string).

    Rows missing a meeting, date, or page id — or with an unparseable date — are
    skipped and counted in ``stats["errors"]``.

    Returns:
        Mapping of (meeting_name, date_str) to the rows for that document.
    """
    documents_map: dict[tuple[str, str], list[dict[str, Any]]] = {}

    for row in rows:
        try:
            meeting_name = row.get("meeting", "")
            date_str = row.get("date", "")
            page_id = row.get("id", "")

            if not meeting_name or not date_str or not page_id:
                logger.warning(f"Skipping row with missing data: {row}")
                stats["errors"] += 1
                continue

            # Validate date format
            date.fromisoformat(date_str)

            documents_map.setdefault((meeting_name, date_str), []).append(row)

        except (ValueError, TypeError) as e:
            logger.warning(f"Error processing row {row}: {e}")
            stats["errors"] += 1

    return documents_map


def get_or_create_document(
    muni: Muni, meeting_name: str, date_str: str, document_type: str
) -> tuple[MeetingDocument, bool]:
    """Get or create the MeetingDocument for a (meeting, date, type) triple."""
    document, created = MeetingDocument.objects.update_or_create(
        municipality=muni,
        meeting_name=meeting_name,
        meeting_date=date.fromisoformat(date_str),
        document_type=document_type,
    )
    return document, created


def build_pages(
    document: MeetingDocument,
    pages_data: list[dict[str, Any]],
    stats: dict[str, int],
) -> list[MeetingPage]:
    """
    Build (but do not save) MeetingPage instances for a document's rows.

    ``bulk_create`` bypasses ``MeetingPage.save()``, so denormalized columns are
    populated here explicitly. Rows without an id are skipped and counted as
    errors.
    """
    by_id: dict[str, MeetingPage] = {}
    for page_data in pages_data:
        page_id = page_data.get("id")
        if not page_id:
            logger.warning(f"Skipping page with no ID: {page_data}")
            stats["errors"] += 1
            continue

        page = MeetingPage(
            id=page_id,
            document=document,
            page_number=page_data.get("page", 0),
            text=page_data.get("text", ""),
            page_image=page_data.get("page_image", ""),
        )
        page.denormalize(document)
        by_id[page_id] = page

    return list(by_id.values())


def bulk_upsert_pages(pages: list[MeetingPage], stats: dict[str, int]) -> None:
    """
    Upsert pages in one statement and update created/updated counts in ``stats``.

    ``pages`` must be non-empty and already denormalized (see ``build_pages``).
    """
    page_ids = [page.id for page in pages]
    existing_ids = set(
        MeetingPage.objects.filter(id__in=page_ids).values_list("id", flat=True)
    )
    stats["pages_created"] += sum(1 for page in pages if page.id not in existing_ids)
    stats["pages_updated"] += sum(1 for page in pages if page.id in existing_ids)

    MeetingPage.objects.bulk_create(
        pages,
        update_conflicts=True,
        unique_fields=["id"],
        update_fields=PAGE_UPSERT_UPDATE_FIELDS,
    )


def _process_rows_batch(
    muni: Muni, rows: list[dict[str, Any]], document_type: str, stats: dict[str, int]
) -> None:
    """
    Process a batch of rows from the civic.band API.

    Args:
        muni: Municipality instance
        rows: List of row dictionaries from the API
        document_type: Type of document ('agenda' or 'minutes')
        stats: Statistics dictionary to update
    """
    documents_map = group_rows_by_document(rows, stats)

    # Create or update documents and their pages
    for (meeting_name, date_str), pages_data in documents_map.items():
        try:
            with transaction.atomic():
                document, created = get_or_create_document(
                    muni, meeting_name, date_str, document_type
                )

                if created:
                    stats["documents_created"] += 1
                else:
                    stats["documents_updated"] += 1

                pages = build_pages(document, pages_data, stats)
                if not pages:
                    continue

                bulk_upsert_pages(pages, stats)

        except Exception as e:
            logger.error(
                f"Error creating document for {meeting_name} on {date_str}: {e}",
                exc_info=True,
            )
            stats["errors"] += 1
