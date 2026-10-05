from typing import Any

from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.http import require_GET
from django.views.generic import TemplateView

from municipalities.models import Muni
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


def _build_push_url(request: HttpRequest) -> str | None:
    """
    Build the URL HTMX should push into browser history for this search.

    Keeps shared/bookmarkable URLs pointing at the full search page (or the
    public topic page) instead of the partial results endpoint. Returns None
    when pushing should be suppressed (e.g. auto-executed searches where the
    URL already reflects the search).
    """
    if request.headers.get("X-No-Push") == "true":
        return None

    params = request.GET.copy()

    base_url = reverse("meetings:meeting-search")
    public_page_slug = params.pop("public_page_slug", None)
    if public_page_slug:
        from searches.models import PublicSearchPage

        public_page = PublicSearchPage.objects.filter(
            slug=public_page_slug[0], is_published=True
        ).first()
        if public_page:
            base_url = public_page.get_absolute_url()

    query_string = params.urlencode()
    return f"{base_url}?{query_string}" if query_string else base_url


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
            public_page = (
                PublicSearchPage.objects.select_related("search")
                .prefetch_related(
                    "search__municipalities",
                    "allowed_municipalities",
                )
                .get(slug=public_page_slug, is_published=True)
            )

            locked_search = public_page.search

            # A locked page owns its search term; ignore whatever the client
            # sent so arbitrary full-corpus queries are impossible.
            if public_page.lock_search_term and locked_search.search_term:
                query = locked_search.search_term

            # Intersect the page's base Search municipality scope with the
            # page's allowed municipalities. If either restricts, the result
            # set is restricted even when the user supplies no filter.
            search_muni_ids = set(
                locked_search.municipalities.values_list("id", flat=True)
            )
            allowed_muni_ids = set(
                public_page.allowed_municipalities.values_list("id", flat=True)
            )
            if search_muni_ids and allowed_muni_ids:
                scoped_muni_ids = search_muni_ids & allowed_muni_ids
            else:
                scoped_muni_ids = search_muni_ids or allowed_muni_ids
            if scoped_muni_ids:
                if municipalities:
                    municipalities = municipalities.filter(id__in=scoped_muni_ids)
                else:
                    municipalities = Muni.objects.filter(id__in=scoped_muni_ids)

            # Enforce state scope the same way.
            if public_page.allowed_states:
                if states:
                    states = [s for s in states if s in public_page.allowed_states]
                else:
                    states = list(public_page.allowed_states)

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

    response = HttpResponse(
        render_to_string(
            "meetings/partials/search_results.html",
            context,
            request=request,
        )
    )
    push_url = _build_push_url(request)
    if push_url:
        response["HX-Push-Url"] = push_url
    return response
