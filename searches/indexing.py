"""
Indexing utilities for Meilisearch.

This module provides functions to convert Django model instances to
Meilisearch documents and handle indexing operations.
"""

from typing import Any

from meetings.models import MeetingPage

from .meilisearch_client import (
    delete_meeting_page,
    index_meeting_page,
    index_meeting_pages_batch,
)


def meeting_page_to_document(page: MeetingPage) -> dict[str, Any]:
    """
    Convert a MeetingPage instance to a Meilisearch document.

    This defines the schema for meeting pages in Meilisearch.
    All searchable, filterable, and displayable fields are included.

    Args:
        page: MeetingPage instance

    Returns:
        Dictionary ready for Meilisearch indexing
    """
    return {
        "id": page.id,
        "page_number": page.page_number,
        "text": page.text,
        "page_image": page.page_image,
        # Document fields
        "document_id": str(page.document.id),
        "meeting_name": page.document.meeting_name,
        "meeting_date": page.document.meeting_date.isoformat(),
        "document_type": page.document.document_type,
        # Municipality fields
        "municipality_id": str(page.document.municipality.id),
        "municipality_subdomain": page.document.municipality.subdomain,
        "municipality_name": page.document.municipality.name,
        "state": page.document.municipality.state,
    }


def index_page(page: MeetingPage) -> dict[str, Any]:
    """
    Index a single MeetingPage in Meilisearch.

    Args:
        page: MeetingPage instance to index

    Returns:
        Task info from Meilisearch
    """
    document = meeting_page_to_document(page)
    return index_meeting_page(document)


def index_pages_batch(pages: list[MeetingPage]) -> dict[str, Any]:
    """
    Index multiple MeetingPages in a single batch operation.

    This is more efficient than indexing pages one at a time.

    Args:
        pages: List of MeetingPage instances to index

    Returns:
        Task info from Meilisearch
    """
    documents = [meeting_page_to_document(page) for page in pages]
    return index_meeting_pages_batch(documents)


def remove_page_from_index(page_id: str) -> dict[str, Any]:
    """
    Remove a MeetingPage from the Meilisearch index.

    Args:
        page_id: ID of the page to remove

    Returns:
        Task info from Meilisearch
    """
    return delete_meeting_page(page_id)


def index_queryset_in_batches(
    queryset, batch_size: int = 10000, progress_callback=None
) -> dict[str, Any]:
    """
    Index a queryset of MeetingPages in batches.

    Uses keyset pagination with values() to efficiently fetch and index
    large querysets without creating PostgreSQL temp files or instantiating
    unnecessary model objects.

    Args:
        queryset: QuerySet of MeetingPage objects
        batch_size: Number of pages to index per batch (recommended: 10000-50000)
        progress_callback: Optional callback function(current, total) for progress updates

    Returns:
        Dict with summary statistics
    """
    total = queryset.count()
    indexed = 0
    tasks = []
    last_pk = ""  # MeetingPage uses CharField as primary key

    # Use keyset pagination with values() for maximum performance
    # This avoids ORM overhead and gets just the fields we need
    while True:
        # Get next batch using pk filtering (very efficient, no temp files)
        # Use values() to avoid instantiating model objects
        batch_values = list(
            queryset.filter(pk__gt=last_pk)
            .order_by("pk")
            .values(
                "id",
                "page_number",
                "text",
                "page_image",
                "document__id",
                "document__meeting_name",
                "document__meeting_date",
                "document__document_type",
                "document__municipality__id",
                "document__municipality__subdomain",
                "document__municipality__name",
                "document__municipality__state",
            )[:batch_size]
        )

        if not batch_values:
            break

        # Convert to Meilisearch document format
        documents = [
            {
                "id": row["id"],
                "page_number": row["page_number"],
                "text": row["text"],
                "page_image": row["page_image"],
                "document_id": str(row["document__id"]),
                "meeting_name": row["document__meeting_name"],
                "meeting_date": row["document__meeting_date"].isoformat(),
                "document_type": row["document__document_type"],
                "municipality_id": str(row["document__municipality__id"]),
                "municipality_subdomain": row["document__municipality__subdomain"],
                "municipality_name": row["document__municipality__name"],
                "state": row["document__municipality__state"],
            }
            for row in batch_values
        ]

        # Index this batch directly
        task = index_meeting_pages_batch(documents)
        tasks.append(task)
        indexed += len(batch_values)
        last_pk = batch_values[-1]["id"]

        if progress_callback:
            progress_callback(indexed, total)

    return {
        "total": total,
        "indexed": indexed,
        "batches": len(tasks),
        "batch_size": batch_size,
        "tasks": tasks,
    }
