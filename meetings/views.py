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
    queryset, municipality=None, date_from=None, date_to=None, document_type=None
):
    """
    Apply filter parameters to the meeting pages queryset.

    Args:
        queryset: Base MeetingPage queryset
        municipality: Optional municipality to filter by
        date_from: Optional start date for meeting date range
        date_to: Optional end date for meeting date range
        document_type: Optional document type ('agenda' or 'minutes')

    Returns:
        Filtered queryset
    """
    if municipality:
        queryset = queryset.filter(document__municipality=municipality)

    if date_from:
        queryset = queryset.filter(document__meeting_date__gte=date_from)

    if date_to:
        queryset = queryset.filter(document__meeting_date__lte=date_to)

    if document_type:
        queryset = queryset.filter(document__document_type=document_type)

    return queryset


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

    # Annotate with search rank using pre-computed search_vector
    # This is MUCH faster than creating SearchVector at query time
    # Filter out very low rank results (weak/irrelevant matches)
    queryset = (
        queryset.annotate(
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
    results_with_headlines = []

    for result in page_results:
        # Annotate each result individually with headline and rank
        annotated = (
            MeetingPage.objects.filter(pk=result.pk)
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
            .first()
        )

        if annotated:
            results_with_headlines.append(annotated)

    return results_with_headlines


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
    municipality = form.cleaned_data.get("municipality")
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
        municipality=municipality,
        date_from=date_from,
        date_to=date_to,
        document_type=document_type,
    )

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
        "municipality": municipality,
        "date_from": date_from,
        "date_to": date_to,
        "document_type": document_type,
    }

    return HttpResponse(
        render_to_string(
            "meetings/partials/search_results.html",
            context,
            request=request,
        )
    )
