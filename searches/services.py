"""
Shared search service for executing searches against local MeetingPage database.

This module provides reusable search functions used by both:
- Page search interface (meetings/views.py)
- Saved search system (searches/models.py)

Uses PgSearchBackend (ParadeDB pg_search BM25) for all searches.
"""

import logging

from meetings.models import MeetingPage

logger = logging.getLogger(__name__)


def execute_search(search):
    """
    Execute a Search object against local MeetingPage database.

    Uses PgSearchBackend and returns a QuerySet for backwards compatibility
    with get_new_pages() which chains .filter(created__gte=...) on the result.

    BM25 ordering is lost on the round-trip through id__in. Fine for digests,
    which ask "what's new since T", not "what's most relevant".

    Args:
        search: Search model instance with filter configuration

    Returns:
        QuerySet of MeetingPage objects matching the search criteria.
    """
    from .search_backends import get_search_backend

    backend = get_search_backend()
    results, total = backend.search(
        query_text=search.search_term,
        municipalities=search.municipalities.all(),
        states=search.states,
        date_from=search.date_from,
        date_to=search.date_to,
        document_type=search.document_type,
        meeting_name_query=search.meeting_name_query,
        limit=10000,
    )

    if len(results) == 10000:
        logger.warning(
            "Search %s hit 10,000 result cap — new-page notifications may under-report.",
            search.pk,
        )

    page_ids = [result["id"] for result in results]
    if not page_ids:
        return MeetingPage.objects.none()

    return MeetingPage.objects.filter(id__in=page_ids)


def execute_search_with_backend(search, limit=100, offset=0):
    """
    Execute a Search object using the backend, returning raw results.

    This is the preferred method for new code as it returns lightweight dictionaries
    instead of full Django model instances.

    Automatically uses Redis caching to eliminate database load for repeated queries.

    Args:
        search: Search model instance with filter configuration
        limit: Maximum number of results to return
        offset: Number of results to skip (for pagination)

    Returns:
        Tuple of (results, total_count)
        - results: List of dictionaries with page data
        - total_count: Total number of matching results
    """
    from .search_backends import get_search_backend

    backend = get_search_backend()

    results, total = backend.search_with_cache(
        query_text=search.search_term,
        municipalities=search.municipalities.all(),
        states=search.states,
        date_from=search.date_from,
        date_to=search.date_to,
        document_type=search.document_type,
        meeting_name_query=search.meeting_name_query,
        limit=limit,
        offset=offset,
    )

    return results, total


def get_new_pages(search):
    """
    Get pages that are new since last check (created after last_checked_for_new_pages).

    Args:
        search: Search model instance

    Returns:
        QuerySet of MeetingPage objects created since last check timestamp.
    """
    all_results = execute_search(search)

    if search.last_checked_for_new_pages:
        all_results = all_results.filter(created__gte=search.last_checked_for_new_pages)

    return all_results
