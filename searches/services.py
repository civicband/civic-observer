"""
Shared search service for executing searches against local MeetingPage database.

This module provides reusable search functions used by both:
- Page search interface (meetings/views.py)
- Saved search system (searches/models.py)

The search backend (PostgreSQL or Meilisearch) is configured via SEARCH_BACKEND setting.
"""

import re

from django.conf import settings
from django.contrib.postgres.search import SearchQuery

from meetings.models import MeetingDocument, MeetingPage

# Minimum rank threshold for search results (used for long search terms)
MINIMUM_RANK_THRESHOLD = 0.01

# Pre-compiled regex patterns for query parsing
_QUOTED_PATTERN = re.compile(r'"([^"]+)"')
_QUOTED_REPLACE_PATTERN = re.compile(r'"[^"]+"')
_OPERATOR_PATTERN = re.compile(r"\b(OR|AND|NOT)\b", re.IGNORECASE)


def execute_search(search):
    """
    Execute a Search object against local MeetingPage database.

    Uses the configured search backend (PostgreSQL or Meilisearch).

    Args:
        search: Search model instance with filter configuration

    Returns:
        QuerySet of MeetingPage objects matching the search criteria.
        If search_term is empty/null, returns all pages matching other filters
        (all updates mode).

    Note:
        When SEARCH_BACKEND='meilisearch', this returns a QuerySet reconstructed
        from Meilisearch results for backwards compatibility. For better performance,
        use execute_search_with_backend() which returns raw dictionaries.
    """
    backend_name = getattr(settings, "SEARCH_BACKEND", "postgres")

    if backend_name == "meilisearch":
        # Use Meilisearch but reconstruct QuerySet for backwards compatibility
        from .search_backends import get_search_backend

        backend = get_search_backend()
        results, _ = backend.search(
            query_text=search.search_term,
            municipalities=search.municipalities.all(),
            states=search.states,
            date_from=search.date_from,
            date_to=search.date_to,
            document_type=search.document_type,
            meeting_name_query=search.meeting_name_query,
            limit=10000,  # Large limit for backwards compat
        )

        # Extract IDs and return QuerySet in same order
        page_ids = [result["id"] for result in results]
        if not page_ids:
            return MeetingPage.objects.none()

        # Return QuerySet - order from Meilisearch is lost but this maintains backwards compat
        # For proper ordering, use execute_search_with_backend() instead
        return MeetingPage.objects.filter(id__in=page_ids)
    else:
        # Use PostgreSQL implementation
        # Start with all meeting pages
        queryset = MeetingPage.objects.select_related(
            "document", "document__municipality"
        ).all()

        # Apply filter parameters
        queryset = _apply_search_filters(
            queryset,
            municipalities=search.municipalities.all(),
            states=search.states,
            date_from=search.date_from,
            date_to=search.date_to,
            document_type=search.document_type,
        )

        # Apply meeting name filter (if provided)
        if search.meeting_name_query:
            queryset = _apply_meeting_name_filter(queryset, search.meeting_name_query)

        # Apply full-text search ONLY if search_term is not empty (all updates mode)
        if search.search_term:
            queryset, _ = _apply_full_text_search(queryset, search.search_term)
        else:
            # All updates mode - return all pages matching other filters
            # Order by date descending for most recent first
            queryset = queryset.order_by("-document__meeting_date")

        return queryset


def execute_search_with_backend(search, limit=100, offset=0):
    """
    Execute a Search object using the configured backend, returning raw results.

    This is the preferred method for new code as it returns lightweight dictionaries
    instead of full Django model instances.

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

    results, total = backend.search(
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
    # Execute the search to get current results
    all_results = execute_search(search)

    # Filter by creation timestamp to get only new pages
    if search.last_checked_for_new_pages:
        all_results = all_results.filter(created__gte=search.last_checked_for_new_pages)

    return all_results


# Helper functions extracted from meetings/views.py for reuse


def _apply_search_filters(
    queryset,
    municipalities=None,
    states=None,
    date_from=None,
    date_to=None,
    document_type=None,
):
    """
    Apply filter parameters to the meeting pages queryset.

    Args:
        queryset: Base MeetingPage queryset
        municipalities: Optional queryset or list of municipalities to filter by
        states: Optional list of states/provinces to filter by
        date_from: Optional start date for meeting date range
        date_to: Optional end date for meeting date range
        document_type: Optional document type ('agenda', 'minutes', or 'all')

    Returns:
        Filtered queryset
    """
    if (
        municipalities and municipalities.exists()
        if hasattr(municipalities, "exists")
        else municipalities
    ):
        queryset = queryset.filter(document__municipality__in=municipalities)

    if states:
        queryset = queryset.filter(document__municipality__state__in=states)

    if date_from:
        queryset = queryset.filter(document__meeting_date__gte=date_from)

    if date_to:
        queryset = queryset.filter(document__meeting_date__lte=date_to)

    if document_type and document_type != "all":
        queryset = queryset.filter(document__document_type=document_type)

    return queryset


def _apply_meeting_name_filter(queryset, meeting_name_query):
    """
    Filter pages by meeting name using full-text search.

    Uses 'simple' search configuration for multilingual support.
    Performance: Uses only GIN index (@@ operator) without expensive ts_rank computation.

    Args:
        queryset: MeetingPage queryset to filter
        meeting_name_query: Search query for meeting names (supports websearch syntax)

    Returns:
        Filtered queryset (only pages from documents with matching meeting names)
    """
    if not meeting_name_query:
        return queryset

    # Create search query for meeting names
    meeting_name_search_query = SearchQuery(
        meeting_name_query, search_type="websearch", config="simple"
    )

    # Filter to pages from documents where meeting_name matches
    # Use only @@ operator (GIN index) - no rank computation needed
    matching_doc_ids = MeetingDocument.objects.filter(
        meeting_name_search_vector=meeting_name_search_query
    ).values_list("id", flat=True)

    return queryset.filter(document_id__in=matching_doc_ids)


def _parse_websearch_query(query_text: str) -> tuple[list[str], str]:
    """
    Parse websearch query to extract tokens while preserving structure.

    Extracts all search terms (inside and outside quotes) for analysis,
    while preserving the original query structure for PostgreSQL.

    Args:
        query_text: Websearch query string (may contain quotes, operators)

    Returns:
        Tuple of (tokens_list, original_query):
        - tokens_list: All search terms for threshold analysis
        - original_query: Unchanged query string for PostgreSQL

    Examples:
        >>> _parse_websearch_query('"ICE" OR immigration')
        (['ICE', 'immigration'], '"ICE" OR immigration')

        >>> _parse_websearch_query('affordable housing')
        (['affordable', 'housing'], 'affordable housing')
    """
    # Extract everything inside quotes (these are phrase searches)
    quoted = _QUOTED_PATTERN.findall(query_text)

    # Extract everything outside quotes
    unquoted_text = _QUOTED_REPLACE_PATTERN.sub(" ", query_text)
    # Remove operators (they don't affect threshold calculation)
    unquoted_text = _OPERATOR_PATTERN.sub(" ", unquoted_text)
    # Extract individual words
    unquoted = [t.strip() for t in unquoted_text.split() if t.strip()]

    # Combine all tokens for analysis
    all_tokens = unquoted + quoted

    return all_tokens, query_text


def _get_smart_threshold(tokens: list[str]) -> float:
    """
    Calculate rank threshold based on query token characteristics.

    Short tokens match more documents with lower average relevance, so we use
    moderately higher thresholds to filter noise and improve performance while
    preserving relevant results.

    Performance impact (conservative thresholds to avoid over-filtering):
        - 2 char terms: 0.02 threshold (2x higher) - filters very low quality matches
        - 3 char terms: 0.015 threshold (1.5x higher) - filters noise
        - 4+ char terms: 0.01 threshold (normal) - minimal filtering

    Args:
        tokens: List of search terms extracted from query

    Returns:
        Threshold value (0.01 to 0.02) based on shortest token

    Examples:
        >>> _get_smart_threshold(['ice'])
        0.015  # 3 characters

        >>> _get_smart_threshold(['ice', 'immigration'])
        0.015  # Shortest token (ice) is 3 characters

        >>> _get_smart_threshold(['affordable', 'housing'])
        0.01  # Both tokens are long
    """
    if not tokens:
        return MINIMUM_RANK_THRESHOLD

    # Get shortest token length (limiting factor for precision)
    min_length = min(len(t) for t in tokens)

    # Conservative thresholds that filter noise without losing relevant results
    if min_length <= 2:
        return 0.02  # "or", "to", "be" - extremely common
    elif min_length == 3:
        return 0.015  # "ice", "law", "ada" - very common
    else:
        return MINIMUM_RANK_THRESHOLD  # 0.01 - normal (4+ chars)


def _apply_full_text_search(queryset, query_text):
    """
    Apply full-text search to the queryset using PostgreSQL search.

    Performance optimized: Uses only GIN index (@@ operator) without expensive
    ts_rank computation. Sorted by date for maximum speed.

    Args:
        queryset: MeetingPage queryset to search
        query_text: Search query string (supports websearch syntax)

    Returns:
        Tuple of (filtered_queryset, search_query_object)
        - Queryset is filtered using GIN index and ordered by date
        - SearchQuery object is returned for use in headline generation
    """
    # Create search query using 'simple' config for multilingual support
    search_query = SearchQuery(query_text, search_type="websearch", config="simple")

    # IMPORTANT: Use ONLY the @@ operator (GIN index) - no ts_rank computation
    # This is dramatically faster as it avoids computing rank for every row
    # Sort by date descending for consistent, fast results
    queryset = queryset.filter(
        search_vector=search_query
    ).order_by("-document__meeting_date")  # Uses GIN index via @@ operator

    return queryset, search_query
