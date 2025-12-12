"""
Shared search service for executing searches against local MeetingPage database.

This module provides reusable search functions used by both:
- Page search interface (meetings/views.py)
- Saved search system (searches/models.py)
"""

import logging

from django.contrib.postgres.search import SearchQuery, SearchRank
from django.db.models import F

from meetings.models import MeetingDocument, MeetingPage

logger = logging.getLogger(__name__)

# Minimum rank threshold for search results
MINIMUM_RANK_THRESHOLD = 0.01


def execute_search(search):
    """
    Execute a Search object against local MeetingPage database.

    Args:
        search: Search model instance with filter configuration

    Returns:
        QuerySet of MeetingPage objects matching the search criteria.
        If search_term is empty/null, returns all pages matching other filters
        (all updates mode).
    """
    # Warn if search has no municipalities configured - this can cause
    # the search to return ALL pages instead of filtering by municipality.
    # This typically indicates a data issue from migration 0006.
    municipalities = search.municipalities.all()
    if not municipalities.exists() and not search.states:
        logger.warning(
            "Search %s has no municipalities or states configured. "
            "Results will include ALL municipalities. "
            "Run 'python manage.py repair_searches' to fix.",
            search.id,
        )

    # Start with all meeting pages
    queryset = MeetingPage.objects.select_related(
        "document", "document__municipality"
    ).all()

    # Apply filter parameters
    queryset = _apply_search_filters(
        queryset,
        municipalities=municipalities,
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
    matching_doc_ids = (
        MeetingDocument.objects.annotate(
            meeting_name_rank=SearchRank(
                F("meeting_name_search_vector"), meeting_name_search_query
            )
        )
        .filter(meeting_name_rank__gte=MINIMUM_RANK_THRESHOLD)
        .values_list("id", flat=True)
    )

    return queryset.filter(document_id__in=matching_doc_ids)


def _apply_full_text_search(queryset, query_text):
    """
    Apply full-text search to the queryset using PostgreSQL search.

    Uses 'simple' search configuration for multilingual support.

    Args:
        queryset: MeetingPage queryset to search
        query_text: Search query string (supports websearch syntax)

    Returns:
        Tuple of (filtered_queryset, search_query_object)
        - Queryset is filtered to rank >= MINIMUM_RANK_THRESHOLD and ordered by relevance
        - SearchQuery object is returned for use in headline generation
    """
    # Create search query using 'simple' config for multilingual support
    search_query = SearchQuery(query_text, search_type="websearch", config="simple")

    # Filter using @@ operator FIRST to use the GIN index
    # Then compute rank and filter by minimum threshold
    queryset = (
        queryset.filter(search_vector=search_query)  # Uses GIN index
        .annotate(
            rank=SearchRank(F("search_vector"), search_query),
        )
        .filter(rank__gte=MINIMUM_RANK_THRESHOLD)
        .order_by("-rank", "-document__meeting_date")
    )

    return queryset, search_query
