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


def search_result_ids(search, limit=10000):
    """
    Run a single backend search and return the matching page IDs.

    Callers that need both "new pages" and a result count should use this once
    rather than issuing separate backend queries.

    Args:
        search: Search model instance with filter configuration
        limit: Maximum number of results to fetch

    Returns:
        List of matching MeetingPage IDs (capped at ``limit``).
    """
    from .search_backends import get_search_backend

    backend = get_search_backend()
    results, _total = backend.search(
        query_text=search.search_term,
        municipalities=search.municipalities.all(),
        states=search.states,
        date_from=search.date_from,
        date_to=search.date_to,
        document_type=search.document_type,
        meeting_name_query=search.meeting_name_query,
        limit=limit,
    )

    if len(results) == limit:
        logger.warning(
            "Search %s hit %s result cap — new-page notifications may under-report.",
            search.pk,
            f"{limit:,}",
        )

    return [result["id"] for result in results]


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
    page_ids = search_result_ids(search)
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


def get_new_pages(search, since=None):
    """
    Get pages that are new since last check (created after the cutoff).

    Args:
        search: Search model instance
        since: Optional cutoff timestamp. When provided it is used instead of
            ``search.last_checked_for_new_pages``. This lets callers that check
            several saved searches sharing one Search compute new pages against
            a single pre-batch cutoff, rather than the shared value that the
            first check has already advanced.

    Returns:
        QuerySet of MeetingPage objects created since the cutoff timestamp.
    """
    all_results = execute_search(search)

    cutoff = since if since is not None else search.last_checked_for_new_pages
    if cutoff:
        all_results = all_results.filter(created__gte=cutoff)

    return all_results


def search_new_pages(search, cutoff):
    """
    Run one backend search and split the results into new pages and a total.

    Args:
        search: Search model instance
        cutoff: Only pages created at/after this time count as "new". ``None``
            means every matching page is new.

    Returns:
        Tuple of (new_pages QuerySet, total matching count).
    """
    page_ids = search_result_ids(search)
    matching = MeetingPage.objects.filter(id__in=page_ids)
    new_pages = matching.filter(created__gte=cutoff) if cutoff else matching
    return new_pages, len(page_ids)
