"""
Shared search service for executing searches against local MeetingPage database.

This module provides reusable search functions used by both:
- Page search interface (meetings/views.py)
- Saved search system (searches/models.py)

Uses PgSearchBackend (ParadeDB pg_search BM25) for all searches.
"""

import logging

from django.conf import settings

from meetings.models import MeetingPage

from .queryspec import QuerySpec

logger = logging.getLogger(__name__)


def search_result_ids(search, limit=10000, modified_after=None):
    """Run a backend search and return matching page ids.

    ``limit=None`` means "no 10k cap": page through the backend until it is
    exhausted, with ``settings.SEARCH_MAX_RESULT_IDS`` as the memory ceiling.
    The cutoff, when given, is applied inside the BM25 query on
    ``MeetingPage.modified``, so a re-OCR'd page can count as new.
    """
    from .search_backends import get_search_backend

    backend = get_search_backend()
    spec = QuerySpec.from_search(search, modified_after=modified_after)

    if limit is None:
        remaining = settings.SEARCH_MAX_RESULT_IDS
        page_size = settings.SEARCH_RESULT_PAGE_SIZE
    else:
        remaining = limit
        page_size = limit

    ids: list[str] = []
    offset = 0
    while remaining > 0:
        fetch = min(page_size, remaining)
        results, _total = backend.search(
            query_text=spec.text,
            municipalities=list(spec.municipality_ids),
            states=list(spec.states),
            date_from=spec.date_from,
            date_to=spec.date_to,
            document_type=spec.document_type,
            meeting_name_query=spec.meeting_name,
            modified_after=spec.modified_after,
            limit=fetch,
            offset=offset,
        )
        ids.extend(result["id"] for result in results)
        remaining -= len(results)
        offset += len(results)
        if len(results) < fetch:
            break

    if limit is None and remaining == 0:
        logger.warning(
            "Search %s hit the %s result ceiling — new-page notifications may "
            "under-report. Narrow the search or raise SEARCH_MAX_RESULT_IDS.",
            search.pk,
            f"{settings.SEARCH_MAX_RESULT_IDS:,}",
        )
    elif limit is not None and len(ids) >= limit:
        logger.warning(
            "Search %s hit %s result cap.",
            search.pk,
            f"{limit:,}",
        )

    return ids


def execute_search(search):
    """
    Execute a Search object against local MeetingPage database.

    Uses PgSearchBackend and returns a QuerySet of the matching pages, capped
    at 10,000 ids for interactive and admin callers.

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
    Get pages that are new since last check (modified after the cutoff).

    Args:
        search: Search model instance
        since: Optional cutoff timestamp. When provided it is used instead of
            ``search.last_checked_for_new_pages``. This lets callers that check
            several saved searches sharing one Search compute new pages against
            a single pre-batch cutoff, rather than the shared value that the
            first check has already advanced.

    Returns:
        QuerySet of MeetingPage objects modified since the cutoff timestamp.
    """
    cutoff = since if since is not None else search.last_checked_for_new_pages
    new_pages, _total = search_new_pages(search, cutoff)
    return new_pages


def _pages_for_ids(page_ids):
    if not page_ids:
        return MeetingPage.objects.none()
    # One array parameter, not one bind per id: Postgres caps a single
    # statement at 65,535 parameters, and an uncapped alert scan can exceed it.
    return MeetingPage.objects.extra(
        where=["meetings_meetingpage.id = ANY(%s)"], params=[list(page_ids)]
    )


def search_new_pages(search, cutoff):
    """Return pages matching ``search`` whose ``modified`` is at/after ``cutoff``.

    The cutoff is pushed into the BM25 query, so there is no 10,000-id cap and
    no second ``created`` filter in Python. ``cutoff=None`` means every match.

    Returns ``(new_pages, total)`` where ``total`` is the number of pages
    matching the search, capped at 10,000 and deliberately computed without the
    cutoff so ``Search.last_result_count`` keeps meaning "pages matching this
    search", not "pages new since the last check". On the first run
    (``cutoff is None``) the same capped query supplies both values.

    Note: ``modified`` advances on every backfill re-upsert, so a backfill may
    re-report unchanged pages as new. Research sets dedupe with ``ON CONFLICT``;
    saved searches do not yet.
    """
    if cutoff is None:
        page_ids = search_result_ids(search, limit=10000)
        return _pages_for_ids(page_ids), len(page_ids)

    new_ids = search_result_ids(search, limit=None, modified_after=cutoff)
    total_ids = search_result_ids(search, limit=10000)
    return _pages_for_ids(new_ids), len(total_ids)
