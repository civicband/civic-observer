import logging

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Prefetch
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.loader import render_to_string
from django.views import View
from django.views.generic import TemplateView

from meetings.models import MeetingPage
from research.add_to_set import add_page_to_set, default_set_for_user
from research.models import Code, Project, ResultSet

from .services import FetchError, fetch_single_page

logger = logging.getLogger(__name__)


class ClipView(LoginRequiredMixin, TemplateView):
    """
    Clip endpoint for saving meeting pages from civic.band to notebooks.

    Accepts query params:
    - id: civic.band page ID
    - subdomain: municipality subdomain
    - table: "agendas" or "minutes"
    """

    template_name = "clip/clip.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["page_id"] = self.request.GET.get("id", "")
        context["subdomain"] = self.request.GET.get("subdomain", "")
        context["table"] = self.request.GET.get("table", "")
        return context


class FetchPageView(LoginRequiredMixin, View):
    """
    HTMX endpoint to fetch and display a meeting page preview.

    Looks up the page by composite key (id + subdomain + document_type).
    If not found locally, attempts to fetch from civic.band API.
    """

    def get(self, request):
        page_id = request.GET.get("id", "")
        subdomain = request.GET.get("subdomain", "")
        table = request.GET.get("table", "")  # "agendas" or "minutes"

        # Validate params
        if not all([page_id, subdomain, table]):
            return self._render_error("Missing required parameters.")

        # Try to find the page locally
        try:
            page = MeetingPage.objects.get(id=page_id)
            return self._render_preview(request, page, subdomain, table)
        except MeetingPage.DoesNotExist:
            pass  # Will try remote fetch

        # Try to fetch from civic.band API
        try:
            fetched_page = fetch_single_page(page_id, subdomain, table)
            if fetched_page:
                return self._render_preview(request, fetched_page, subdomain, table)
            else:
                return self._render_error(
                    "Couldn't find this meeting. Please check the link and try again.",
                    subdomain=subdomain,
                )
        except FetchError as e:
            logger.error(f"Failed to fetch page {page_id}: {e}")
            return self._render_error(
                "Couldn't find this meeting. Please check the link and try again.",
                subdomain=subdomain,
            )

    def _render_preview(self, request, page, subdomain, table):
        """Render the page preview with the add-to-set form."""
        projects = (
            Project.objects.filter(owner=request.user, is_archived=False)
            .prefetch_related(
                "sets",
                Prefetch(
                    "codes",
                    queryset=Code.objects.filter(is_archived=False),
                    to_attr="active_codes",
                ),
            )
            .order_by("-modified")
        )
        html = render_to_string(
            "clip/partials/page_preview.html",
            {
                "page": page,
                "subdomain": subdomain,
                "table": table,
                "projects": projects,
            },
            request=request,
        )
        return HttpResponse(html)

    def _render_error(self, message, subdomain=None):
        """Render an error message."""
        html = render_to_string(
            "clip/partials/error.html",
            {
                "message": message,
                "subdomain": subdomain,
            },
        )
        return HttpResponse(html)


class SavePageView(LoginRequiredMixin, View):
    """
    Add a clipped meeting page to a research set and redirect to the set.

    Uses the selected set when provided, otherwise the user's default set.
    """

    def post(self, request):
        page_id = request.POST.get("page_id")
        set_id = request.POST.get("set")
        note = request.POST.get("note", "").strip()

        page = get_object_or_404(MeetingPage, id=page_id)

        if set_id:
            result_set = get_object_or_404(
                ResultSet, id=set_id, project__owner=request.user
            )
        else:
            result_set = default_set_for_user(request.user)

        add_page_to_set(
            result_set.project,
            result_set,
            page,
            actor=request.user,
            note=note,
            code_names=request.POST.getlist("codes"),
        )
        return redirect(
            "research:set-detail",
            project=result_set.project_id,
            set=result_set.id,
        )
