import re
from typing import Any

from django.contrib.postgres.search import (
    SearchHeadline,
    SearchQuery,
    SearchRank,
)
from django.core.paginator import Paginator
from django.db.models import F
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.http import require_GET
from django.views.generic import TemplateView

from .forms import MeetingSearchForm
from .models import MeetingPage

# Search pagination and display constants
SEARCH_RESULTS_PER_PAGE = 20

# Minimum rank threshold for search results (used for long search terms)
# Results with rank below this value will be filtered out
MINIMUM_RANK_THRESHOLD = 0.01

# Pre-compiled regex patterns for query parsing
_QUOTED_PATTERN = re.compile(r'"([^"]+)"')
_QUOTED_REPLACE_PATTERN = re.compile(r'"[^"]+"')
_OPERATOR_PATTERN = re.compile(r"\b(OR|AND|NOT)\b", re.IGNORECASE)

# SearchHeadline configuration for result previews
HEADLINE_START_TAG = "<mark class='bg-yellow-200 font-semibold'>"
HEADLINE_STOP_TAG = "</mark>"
HEADLINE_MAX_WORDS = 50
HEADLINE_MIN_WORDS = 15
HEADLINE_SHORT_WORD_LENGTH = "3"
HEADLINE_MAX_FRAGMENTS = 3
HEADLINE_FRAGMENT_DELIMITER = " ... "


class MeetingSearchView(TemplateView):
    """Main view for searching meeting documents with full-text search."""

    template_name = "meetings/meeting_search.html"

    def dispatch(self, request, *args, **kwargs):
        """Require authentication for meeting search."""
        if not request.user.is_authenticated:
            from django.contrib.auth.views import redirect_to_login

            return redirect_to_login(request.get_full_path())
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form"] = MeetingSearchForm(self.request.GET or None)
        context["has_query"] = bool(self.request.GET.get("query"))
        return context


# Helper functions for search views


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
        municipalities: Optional list of municipalities to filter by
        states: Optional list of states/provinces to filter by
        date_from: Optional start date for meeting date range
        date_to: Optional end date for meeting date range
        document_type: Optional document type ('agenda' or 'minutes')

    Returns:
        Filtered queryset
    """
    if municipalities:
        queryset = queryset.filter(document__municipality__in=municipalities)

    if states:
        queryset = queryset.filter(document__municipality__state__in=states)

    if date_from:
        queryset = queryset.filter(document__meeting_date__gte=date_from)

    if date_to:
        queryset = queryset.filter(document__meeting_date__lte=date_to)

    if document_type:
        queryset = queryset.filter(document__document_type=document_type)

    return queryset


def _apply_meeting_name_filter(queryset, meeting_name_query):
    """
    Filter pages by meeting name using full-text search.

    Uses 'simple' search configuration for multilingual support (works across
    Spanish, English, and other languages without language-specific stemming).

    Performance: Uses JOIN instead of subquery for better query planning.

    Args:
        queryset: MeetingPage queryset to filter
        meeting_name_query: Search query for meeting names (supports websearch syntax: phrases, AND, OR, NOT)

    Returns:
        Filtered queryset (only pages from documents with matching meeting names)
    """
    if not meeting_name_query:
        return queryset

    # Create search query for meeting names
    meeting_name_search_query = SearchQuery(
        meeting_name_query, search_type="websearch", config="simple"
    )

    # Use JOIN and filter on the document relationship (much faster than subquery)
    # PostgreSQL can optimize this join pattern better than IN (subquery)
    queryset = (
        queryset.filter(document__meeting_name_search_vector=meeting_name_search_query)
        .annotate(
            meeting_name_rank=SearchRank(
                F("document__meeting_name_search_vector"), meeting_name_search_query
            )
        )
        .filter(meeting_name_rank__gte=MINIMUM_RANK_THRESHOLD)
    )

    return queryset


def _parse_websearch_query(query_text: str) -> tuple[list[str], str]:
    """
    Parse websearch query to extract tokens while preserving structure.

    Extracts all search terms (inside and outside quotes) for analysis,
    while preserving the original query structure for PostgreSQL.

    Args:
        query_text: Original search query with websearch syntax

    Returns:
        Tuple of (tokens, original_query) where:
        - tokens: list of all search terms for threshold calculation
        - original_query: unchanged query to pass to PostgreSQL

    Examples:
        >>> _parse_websearch_query('"ICE" OR immigration')
        (['ICE', 'immigration'], '"ICE" OR immigration')

        >>> _parse_websearch_query('affordable housing AND rent')
        (['affordable', 'housing', 'rent'], 'affordable housing AND rent')
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
    higher thresholds to filter noise and improve performance.

    Args:
        tokens: List of search terms extracted from query

    Returns:
        Threshold value for ts_rank filtering

    Performance impact:
        - 2 char terms: 0.20 threshold (20x higher) - filters ~95% of matches
        - 3 char terms: 0.12 threshold (12x higher) - filters ~90% of matches
        - 4 char terms: 0.06 threshold (6x higher) - filters ~70% of matches
        - 5+ char terms: 0.01 threshold (normal) - minimal filtering
    """
    if not tokens:
        return MINIMUM_RANK_THRESHOLD

    # Get shortest token length (limiting factor for precision)
    min_length = min(len(t) for t in tokens)

    # Aggressive thresholds for very short terms
    if min_length <= 2:
        return 0.20  # "or", "to", "be" - extremely common
    elif min_length == 3:
        return 0.12  # "ice", "law", "ada" - very common
    elif min_length == 4:
        return 0.06  # "rent", "park" - common
    else:
        return MINIMUM_RANK_THRESHOLD  # 0.01 - normal


def _apply_full_text_search(queryset, query_text):
    """
    Apply full-text search to the queryset using PostgreSQL search.

    Uses 'simple' search configuration for multilingual support (works across
    Spanish, English, and other languages without language-specific stemming).

    Performance: Annotates search_rank which can be reused later to avoid
    recalculating expensive ts_rank function. Uses smart thresholds based on
    query characteristics to dramatically improve performance for short terms.

    Args:
        queryset: MeetingPage queryset to search
        query_text: Search query string (supports websearch syntax: phrases, AND, OR, NOT)

    Returns:
        Tuple of (filtered_queryset, search_query_object)
        - Queryset is filtered to rank >= smart threshold and ordered by relevance
        - QuerySet includes 'search_rank' annotation for reuse
        - SearchQuery object is returned for use in headline generation
    """
    # Parse query to extract tokens (for threshold calculation only)
    # Original query structure is preserved for PostgreSQL
    tokens, original_query = _parse_websearch_query(query_text)

    # Calculate smart threshold based on shortest token
    # Short terms need higher thresholds to filter noise
    threshold = _get_smart_threshold(tokens)

    # Create search query using 'simple' config for multilingual support
    # Pass original query unchanged to preserve operators and quoted phrases
    search_query = SearchQuery(original_query, search_type="websearch", config="simple")

    # IMPORTANT: Filter using @@ operator FIRST to use the GIN index
    # This dramatically reduces rows before computing expensive ts_rank
    # Only then compute rank and filter by smart threshold
    # Annotate as 'search_rank' (not 'rank') so it can be reused later
    queryset = (
        queryset.filter(search_vector=search_query)  # Uses GIN index via @@ operator
        .annotate(
            search_rank=SearchRank(F("search_vector"), search_query),
        )
        .filter(search_rank__gte=threshold)
        .order_by("-search_rank", "-document__meeting_date")
    )

    return queryset, search_query


def _generate_headlines_for_page(page_results, search_query):
    """
    Generate search result headlines for a page of results.

    This is done AFTER pagination to avoid expensive headline generation
    for results that won't be displayed. Generates highlighted text snippets
    showing where search terms appear in the document.

    Performance: Reuses search_rank from page_results if available to avoid
    recalculating expensive ts_rank function.

    Args:
        page_results: List of MeetingPage objects from current page (may include search_rank annotation)
        search_query: SearchQuery object used for highlighting matches

    Returns:
        List of MeetingPage objects with headline and search_rank annotations
    """
    # Extract PKs from page results
    page_pks = [result.pk for result in page_results]

    # Check if page_results already have search_rank annotation
    # If so, we can reuse it instead of recalculating
    has_rank = hasattr(page_results[0], "search_rank") if page_results else False

    # Single query to fetch all headlines for the page
    # This is MUCH faster than querying each result individually (N+1 problem)
    queryset = MeetingPage.objects.filter(pk__in=page_pks)

    # Only compute rank if not already present (avoids expensive recalculation)
    if not has_rank:
        queryset = queryset.annotate(
            search_rank=SearchRank(F("search_vector"), search_query),
        )

    results_with_headlines = queryset.annotate(
        headline=SearchHeadline(
            "text",
            search_query,
            start_sel=HEADLINE_START_TAG,
            stop_sel=HEADLINE_STOP_TAG,
            max_words=HEADLINE_MAX_WORDS,
            min_words=HEADLINE_MIN_WORDS,
            short_word=HEADLINE_SHORT_WORD_LENGTH,
            highlight_all=False,
            max_fragments=HEADLINE_MAX_FRAGMENTS,
            fragment_delimiter=HEADLINE_FRAGMENT_DELIMITER,
            config="simple",
        ),
    ).select_related("document", "document__municipality")

    # Preserve original ordering and include rank from original if it exists
    results_dict = {result.pk: result for result in results_with_headlines}
    final_results = []
    for i, pk in enumerate(page_pks):
        if pk in results_dict:
            result = results_dict[pk]
            # If original had rank but new query didn't, copy it over
            if has_rank and not hasattr(result, "search_rank"):
                result.search_rank = page_results[i].search_rank  # type: ignore[attr-defined]
            final_results.append(result)
    return final_results


def _is_htmx_request(request: HttpRequest) -> bool:
    """Check if this is an HTMX request."""
    return request.headers.get("HX-Request") == "true"


@require_GET
def meeting_page_search_results(request: HttpRequest) -> HttpResponse:
    """
    Search meeting pages with full-text search and filters.

    Handles both HTMX requests (returns partial) and regular requests (redirects
    to main search page with results).

    Security: Requires authentication UNLESS request includes a valid public_page_slug
    for a published PublicSearchPage.
    """
    # Check authentication - allow if:
    # 1. User is authenticated (regular search), OR
    # 2. Request has valid public_page_slug (public search page)
    public_page_slug = request.GET.get("public_page_slug")
    is_public_search = False

    if public_page_slug:
        # Verify this is a valid published public search page
        from searches.models import PublicSearchPage

        try:
            PublicSearchPage.objects.get(slug=public_page_slug, is_published=True)
            is_public_search = True
        except PublicSearchPage.DoesNotExist:
            pass

    # Require authentication if not a public search
    if not is_public_search and not request.user.is_authenticated:
        from django.contrib.auth.views import redirect_to_login

        return redirect_to_login(request.get_full_path())

    # For non-HTMX requests (e.g., when JavaScript fails to load on mobile),
    # redirect to the main search page with query params preserved.
    # The main page will then trigger the HTMX search on load.
    if not _is_htmx_request(request):
        # Build URL with existing query parameters
        query_string = request.GET.urlencode()
        base_url = reverse("meetings:meeting-search")
        redirect_url = f"{base_url}?{query_string}" if query_string else base_url
        return redirect(redirect_url)

    form = MeetingSearchForm(request.GET)

    # Default empty context
    context: dict[str, Any] = {
        "results": [],
        "page_obj": None,
        "has_query": False,
        "error": None,
    }

    if not form.is_valid():
        context["error"] = "Invalid search parameters. Please check your filters."
        return HttpResponse(
            render_to_string(
                "meetings/partials/search_results.html",
                context,
                request=request,
            )
        )

    query = form.cleaned_data.get("query", "").strip()
    meeting_name_query = form.cleaned_data.get("meeting_name_query", "").strip()
    municipalities = form.cleaned_data.get("municipalities")
    states = form.cleaned_data.get("states")
    date_from = form.cleaned_data.get("date_from")
    date_to = form.cleaned_data.get("date_to")
    document_type = form.cleaned_data.get("document_type")

    # Check if this request is from a public search page and enforce scope limits
    public_page_slug = request.GET.get("public_page_slug")
    if public_page_slug:
        from searches.models import PublicSearchPage

        try:
            public_page = PublicSearchPage.objects.prefetch_related(
                "allowed_municipalities"
            ).get(slug=public_page_slug, is_published=True)

            # Enforce municipality scope
            if public_page.allowed_municipalities.exists():
                allowed_muni_ids = set(
                    public_page.allowed_municipalities.values_list("id", flat=True)
                )
                if municipalities:
                    # Filter to only allowed municipalities
                    municipalities = municipalities.filter(id__in=allowed_muni_ids)
                # Note: If no municipalities selected, we don't auto-add them
                # to keep the search "wide" within scope

            # Enforce state scope
            if public_page.allowed_states and states:
                states = [s for s in states if s in public_page.allowed_states]

            # Enforce date scope
            if public_page.min_date:
                if not date_from or date_from < public_page.min_date:
                    date_from = public_page.min_date
            if public_page.max_date:
                if not date_to or date_to > public_page.max_date:
                    date_to = public_page.max_date

        except PublicSearchPage.DoesNotExist:
            # Invalid slug - continue without scope enforcement
            pass

    # Require a search query
    if not query:
        context["error"] = "Please enter a search term to search meeting documents."
        return HttpResponse(
            render_to_string(
                "meetings/partials/search_results.html",
                context,
                request=request,
            )
        )

    # Mark that we have a query for template
    context["has_query"] = True

    # Start with all meeting pages
    queryset = MeetingPage.objects.select_related(
        "document", "document__municipality"
    ).all()

    # Performance optimization: Apply full-text search FIRST (most selective filter)
    # This dramatically reduces the dataset before applying other filters and joins
    queryset, search_query = _apply_full_text_search(queryset, query)

    # Apply meeting name filter (if provided)
    # This is applied after full-text search but before other filters
    # because it uses the document join which is already loaded
    queryset = _apply_meeting_name_filter(queryset, meeting_name_query)

    # Apply remaining filter parameters to the already-reduced dataset
    queryset = _apply_search_filters(
        queryset,
        municipalities=municipalities,
        states=states,
        date_from=date_from,
        date_to=date_to,
        document_type=document_type,
    )

    # Paginate results
    # IMPORTANT: Paginate BEFORE generating headlines for performance
    paginator = Paginator(queryset, SEARCH_RESULTS_PER_PAGE)
    page_number = request.GET.get("page", 1)
    page_obj = paginator.get_page(page_number)

    # Generate headlines ONLY for the current page (not all results)
    # This is a major performance optimization - headlines are expensive to compute
    context["results"] = _generate_headlines_for_page(
        page_obj.object_list, search_query
    )
    context["page_obj"] = page_obj

    # Add active filters to context for display
    context["active_filters"] = {
        "query": query,
        "meeting_name_query": meeting_name_query,
        "municipalities": municipalities,
        "states": states,
        "date_from": date_from,
        "date_to": date_to,
        "document_type": document_type,
    }

    # Add saved page IDs for authenticated users (for save button state)
    # Performance: Only check if pages on THIS page are saved (not all pages)
    if request.user.is_authenticated and context["results"]:
        from notebooks.models import NotebookEntry

        # Only check the page IDs that are in the current results
        result_page_ids = [r.pk for r in context["results"]]
        saved_page_ids = set(
            NotebookEntry.objects.filter(
                notebook__user=request.user, meeting_page_id__in=result_page_ids
            ).values_list("meeting_page_id", flat=True)
        )
        context["saved_page_ids"] = saved_page_ids
    else:
        context["saved_page_ids"] = set()

    return HttpResponse(
        render_to_string(
            "meetings/partials/search_results.html",
            context,
            request=request,
        )
    )
