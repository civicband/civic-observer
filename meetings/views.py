from typing import Any

from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.http import require_GET
from django.views.generic import TemplateView

from searches.search_backends import get_search_backend

from .forms import MeetingSearchForm
from .models import MeetingPage

# Search pagination and display constants
SEARCH_RESULTS_PER_PAGE = 20


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
        "page_info": None,
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

    try:
        page_number = int(request.GET.get("page", 1))
    except (TypeError, ValueError):
        page_number = 1
    if page_number < 1:
        page_number = 1

    backend = get_search_backend()
    offset = (page_number - 1) * SEARCH_RESULTS_PER_PAGE

    results, total = backend.search_with_cache(
        query_text=query,
        municipalities=municipalities,
        states=states,
        date_from=date_from,
        date_to=date_to,
        document_type=document_type,
        meeting_name_query=meeting_name_query,
        limit=SEARCH_RESULTS_PER_PAGE,
        offset=offset,
    )

    page_ids = [result["id"] for result in results]
    page_results = MeetingPage.objects.filter(id__in=page_ids)

    # Preserve order from search backend
    id_to_result = {pid: idx for idx, pid in enumerate(page_ids)}
    page_results = sorted(page_results, key=lambda p: id_to_result.get(p.id, 0))  # type: ignore[assignment]

    # Attach snippet from backend results to page objects for template use
    snippet_map = {r["id"]: r.get("snippet") for r in results}
    for page in page_results:
        page.snippet = snippet_map.get(page.id)  # type: ignore[attr-defined]

    # Calculate pagination
    has_next = offset + len(results) < total

    page_info = {
        "number": page_number,
        "has_previous": page_number > 1,
        "has_next": has_next,
        "previous_page_number": page_number - 1 if page_number > 1 else None,
        "next_page_number": page_number + 1 if has_next else None,
    }

    context["results"] = page_results
    context["page_info"] = page_info

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
    if request.user.is_authenticated and context["results"]:
        from notebooks.models import NotebookEntry

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
