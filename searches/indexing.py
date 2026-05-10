"""
Indexing utilities for Meilisearch.

This module provides functions to convert Django model instances to
Meilisearch documents and handle indexing operations.
"""

from typing import Any

from meetings.models import MeetingPage


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
