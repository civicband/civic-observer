from typing import Any

from django.contrib.postgres.search import (
    SearchHeadline,
    SearchQuery,
    SearchRank,
)
from django.core.paginator import Paginator
from django.db.models import F
from django.http import HttpRequest, HttpResponse
from django.template.loader import render_to_string
from django.views.decorators.http import require_GET
from django.views.generic import TemplateView

from .forms import MeetingSearchForm
from .models import MeetingPage

# Search pagination and display constants
SEARCH_RESULTS_PER_PAGE = 20

# Minimum rank threshold for search results
# Results with rank below this value will be filtered out
MINIMUM_RANK_THRESHOLD = 0.01

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

    # Filter to pages from documents where meeting_name matches
    # Use subquery to filter by document IDs that match the meeting name search
    from .models import MeetingDocument

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

    Uses 'simple' search configuration for multilingual support (works across
    Spanish, English, and other languages without language-specific stemming).

    Args:
        queryset: MeetingPage queryset to search
        query_text: Search query string (supports websearch syntax: phrases, AND, OR, NOT)

    Returns:
        Tuple of (filtered_queryset, search_query_object)
        - Queryset is filtered to rank >= MINIMUM_RANK_THRESHOLD and ordered by relevance
        - SearchQuery object is returned for use in headline generation
    """
    # Create search query using 'simple' config for multilingual support
    search_query = SearchQuery(query_text, search_type="websearch", config="simple")

    # IMPORTANT: Filter using @@ operator FIRST to use the GIN index
    # This dramatically reduces rows before computing expensive ts_rank
    # Only then compute rank and filter by minimum threshold
    queryset = (
        queryset.filter(search_vector=search_query)  # Uses GIN index via @@ operator
        .annotate(
            rank=SearchRank(F("search_vector"), search_query),
        )
        .filter(rank__gte=MINIMUM_RANK_THRESHOLD)
        .order_by("-rank", "-document__meeting_date")
    )

    return queryset, search_query


def _generate_headlines_for_page(page_results, search_query):
    """
    Generate search result headlines for a page of results.

    This is done AFTER pagination to avoid expensive headline generation
    for results that won't be displayed. Generates highlighted text snippets
    showing where search terms appear in the document.

    Args:
        page_results: List of MeetingPage objects from current page
        search_query: SearchQuery object used for highlighting matches

    Returns:
        List of MeetingPage objects with headline and rank annotations
    """
    # Extract PKs from page results
    page_pks = [result.pk for result in page_results]

    # Single query to fetch all headlines and ranks for the page
    # This is MUCH faster than querying each result individually (N+1 problem)
    results_with_headlines = (
        MeetingPage.objects.filter(pk__in=page_pks)
        .annotate(
            rank=SearchRank(F("search_vector"), search_query),
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
        )
        .select_related("document", "document__municipality")
    )

    # Preserve original ordering
    results_dict = {result.pk: result for result in results_with_headlines}
    return [results_dict[pk] for pk in page_pks if pk in results_dict]


@require_GET
def meeting_page_search_results(request: HttpRequest) -> HttpResponse:
    """HTMX endpoint for searching meeting pages with full-text search and filters."""
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

    # Apply filter parameters
    queryset = _apply_search_filters(
        queryset,
        municipalities=municipalities,
        states=states,
        date_from=date_from,
        date_to=date_to,
        document_type=document_type,
    )

    # Apply meeting name filter (if provided)
    queryset = _apply_meeting_name_filter(queryset, meeting_name_query)

    # Apply full-text search
    queryset, search_query = _apply_full_text_search(queryset, query)

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
    if request.user.is_authenticated:
        from notebooks.models import NotebookEntry

        # Get page IDs that are saved to any of user's notebooks
        saved_page_ids = set(
            NotebookEntry.objects.filter(notebook__user=request.user).values_list(
                "meeting_page_id", flat=True
            )
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
