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
    queryset, batch_size: int = 1000, progress_callback=None
) -> dict[str, Any]:
    """
    Index a queryset of MeetingPages in batches.

    Efficiently processes large querysets by batching documents using
    Django's .iterator() to avoid loading all objects into memory.

    IMPORTANT: Caller should use select_related("document", "document__municipality")
    on the queryset for optimal performance.

    Args:
        queryset: QuerySet of MeetingPage objects (should use select_related)
        batch_size: Number of pages to index per batch
        progress_callback: Optional callback function(current, total) for progress updates

    Returns:
        Dict with summary statistics
    """
    total = queryset.count()
    indexed = 0
    batch = []
    tasks = []

    # Use iterator() to stream results without loading everything into memory
    # This is critical for indexing millions of pages
    for page in queryset.iterator(chunk_size=batch_size):
        batch.append(page)

        if len(batch) >= batch_size:
            task = index_pages_batch(batch)
            tasks.append(task)
            indexed += len(batch)
            batch = []

            if progress_callback:
                progress_callback(indexed, total)

    # Index remaining pages
    if batch:
        task = index_pages_batch(batch)
        tasks.append(task)
        indexed += len(batch)

        if progress_callback:
            progress_callback(indexed, total)

    return {
        "total": total,
        "indexed": indexed,
        "batches": len(tasks),
        "batch_size": batch_size,
        "tasks": tasks,
    }
