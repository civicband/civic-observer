from django.contrib.postgres.search import (
    SearchHeadline,
    SearchQuery,
    SearchRank,
    SearchVector,
)
from django.core.paginator import Paginator
from django.db.models import F
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.views.decorators.http import require_GET
from django.views.generic import TemplateView

from .forms import MeetingSearchForm
from .models import MeetingPage


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


@require_GET
def meeting_page_search_results(request):
    """HTMX endpoint for searching meeting pages with full-text search and filters."""
    form = MeetingSearchForm(request.GET)

    # Default empty context
    context = {
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

    # Start with all meeting pages
    queryset = MeetingPage.objects.select_related(
        "document", "document__municipality"
    ).all()

    # Apply filters
    if municipality:
        queryset = queryset.filter(document__municipality=municipality)

    if date_from:
        queryset = queryset.filter(document__meeting_date__gte=date_from)

    if date_to:
        queryset = queryset.filter(document__meeting_date__lte=date_to)

    if document_type:
        queryset = queryset.filter(document__document_type=document_type)

    # Apply full-text search if query provided
    if query:
        context["has_query"] = True

        # Create search query
        search_query = SearchQuery(query, search_type="websearch")

        # Annotate with search rank and headline
        queryset = (
            queryset.annotate(
                rank=SearchRank(SearchVector("text"), search_query),
                headline=SearchHeadline(
                    "text",
                    search_query,
                    start_sel="<mark class='bg-yellow-200 font-semibold'>",
                    stop_sel="</mark>",
                    max_words=50,
                    min_words=15,
                    short_word=3,
                    highlight_all=False,
                    max_fragments=3,
                    fragment_delimiter=" ... ",
                ),
            )
            .filter(rank__gt=0)
            .order_by("-rank", "-document__meeting_date")
        )
    else:
        # No query, just show recent results ordered by date
        queryset = queryset.annotate(
            headline=F("text")  # Use full text as headline when no search
        ).order_by("-document__meeting_date", "document__meeting_name", "page_number")

    # Paginate results (20 per page)
    paginator = Paginator(queryset, 20)
    page_number = request.GET.get("page", 1)
    page_obj = paginator.get_page(page_number)

    context["results"] = page_obj.object_list
    context["page_obj"] = page_obj

    return HttpResponse(
        render_to_string(
            "meetings/partials/search_results.html",
            context,
            request=request,
        )
    )
