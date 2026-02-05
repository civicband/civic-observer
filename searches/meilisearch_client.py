"""
Meilisearch client and utilities for managing search indexes.

This module provides a centralized interface for interacting with Meilisearch,
including index configuration, document indexing, and search operations.
"""

from typing import TYPE_CHECKING, Any

import meilisearch
from django.conf import settings

if TYPE_CHECKING:
    from meilisearch.index import Index


def get_meilisearch_client() -> meilisearch.Client:
    """
    Get configured Meilisearch client.

    Returns:
        meilisearch.Client: Configured client instance
    """
    return meilisearch.Client(
        settings.MEILISEARCH_URL,
        settings.MEILISEARCH_MASTER_KEY,
    )


def get_index_name(index_key: str) -> str:
    """
    Get the full index name with prefix.

    Args:
        index_key: Index key from settings (e.g., 'meeting_pages')

    Returns:
        Full index name (e.g., 'civic_observer_meeting_pages')
    """
    return f"{settings.MEILISEARCH_INDEX_PREFIX}_{index_key}"


def get_meeting_pages_index() -> "Index":
    """
    Get the meeting pages index.

    Returns:
        Index: The meeting pages index
    """
    client = get_meilisearch_client()
    index_name = get_index_name("meeting_pages")
    return client.index(index_name)


def configure_index(index_key: str) -> Any:
    """
    Configure a Meilisearch index with settings from Django settings.

    This applies the searchable attributes, filterable attributes, sortable attributes,
    ranking rules, and other settings defined in MEILISEARCH_INDEX_SETTINGS.

    Args:
        index_key: Index key from settings (e.g., 'meeting_pages')

    Returns:
        TaskInfo object from Meilisearch

    Raises:
        KeyError: If index_key not found in MEILISEARCH_INDEX_SETTINGS
    """
    if index_key not in settings.MEILISEARCH_INDEX_SETTINGS:
        raise KeyError(f"Index '{index_key}' not found in MEILISEARCH_INDEX_SETTINGS")

    client = get_meilisearch_client()
    index_name = get_index_name(index_key)

    # Create or get the index
    client.create_index(index_name, {"primaryKey": "id"})
    index = client.index(index_name)

    # Get settings for this index
    index_settings = settings.MEILISEARCH_INDEX_SETTINGS[index_key]

    # Apply settings (Meilisearch will queue these as tasks)
    task = index.update_settings(index_settings)

    return task


def index_meeting_page(page_data: dict[str, Any]) -> Any:
    """
    Index a single meeting page document.

    Args:
        page_data: Dictionary with page data (must include 'id' field)

    Returns:
        TaskInfo object from Meilisearch
    """
    index = get_meeting_pages_index()
    task = index.add_documents([page_data])
    return task


def index_meeting_pages_batch(pages_data: list[dict[str, Any]]) -> Any:
    """
    Index multiple meeting page documents in a batch.

    Meilisearch handles batching efficiently - this is the preferred method
    for indexing large numbers of documents.

    Args:
        pages_data: List of dictionaries with page data (each must include 'id' field)

    Returns:
        TaskInfo object from Meilisearch
    """
    if not pages_data:
        return {
            "taskUid": None,
            "status": "skipped",
            "message": "No documents to index",
        }  # type: ignore[return-value]

    index = get_meeting_pages_index()
    task = index.add_documents(pages_data)
    return task


def delete_meeting_page(page_id: str) -> Any:
    """
    Delete a meeting page document from the index.

    Args:
        page_id: ID of the page to delete

    Returns:
        TaskInfo object from Meilisearch
    """
    index = get_meeting_pages_index()
    task = index.delete_document(page_id)
    return task


def delete_all_documents(index_key: str) -> Any:
    """
    Delete all documents from an index.

    Use with caution! This is primarily for testing or complete re-indexing.

    Args:
        index_key: Index key from settings (e.g., 'meeting_pages')

    Returns:
        TaskInfo object from Meilisearch
    """
    client = get_meilisearch_client()
    index_name = get_index_name(index_key)
    index = client.index(index_name)
    task = index.delete_all_documents()
    return task


def get_index_stats(index_key: str) -> Any:
    """
    Get statistics for an index.

    Returns information like number of documents, index size, etc.

    Args:
        index_key: Index key from settings (e.g., 'meeting_pages')

    Returns:
        IndexStats object from Meilisearch
    """
    client = get_meilisearch_client()
    index_name = get_index_name(index_key)
    index = client.index(index_name)
    stats = index.get_stats()
    return stats
