import logging

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.loader import render_to_string
from django.views import View
from django.views.generic import TemplateView

from meetings.models import MeetingPage
from notebooks.models import Notebook, NotebookEntry, Tag

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

        # Convert table to document_type
        document_type = "agenda" if table == "agendas" else "minutes"

        # Try to find the page locally using composite key
        try:
            page = MeetingPage.objects.select_related(
                "document", "document__municipality"
            ).get(
                id=page_id,
                document__municipality__subdomain=subdomain,
                document__document_type=document_type,
            )
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
        """Render the page preview with save form."""
        notebooks = Notebook.objects.filter(
            user=request.user, is_archived=False
        ).order_by("-modified")
        tags = Tag.objects.filter(user=request.user).order_by("name")

        html = render_to_string(
            "clip/partials/page_preview.html",
            {
                "page": page,
                "subdomain": subdomain,
                "table": table,
                "notebooks": notebooks,
                "tags": tags,
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
    Save a meeting page to a notebook and redirect to the notebook.

    Creates a new notebook if new_notebook_name is provided.
    Handles tags (existing and new).
    """

    def post(self, request):
        page_id = request.POST.get("page_id")
        notebook_id = request.POST.get("notebook_id")
        new_notebook_name = request.POST.get("new_notebook_name", "").strip()
        note = request.POST.get("note", "").strip()
        new_tag = request.POST.get("new_tag", "").strip().lower()
        tag_ids = request.POST.getlist("tags")

        # Get the page
        page = get_object_or_404(MeetingPage, id=page_id)

        # Determine target notebook
        if new_notebook_name:
            # Create new notebook
            notebook = Notebook.objects.create(
                user=request.user,
                name=new_notebook_name,
            )
        elif notebook_id:
            notebook = get_object_or_404(Notebook, id=notebook_id, user=request.user)
        else:
            # Use most recently modified notebook, or create default
            notebook_or_none = (
                Notebook.objects.filter(user=request.user, is_archived=False)
                .order_by("-modified")
                .first()
            )
            if not notebook_or_none:
                notebook = Notebook.objects.create(
                    user=request.user,
                    name="My Notebook",
                )
            else:
                notebook = notebook_or_none

        # Check if already saved to this notebook
        existing = NotebookEntry.objects.filter(
            notebook=notebook,
            meeting_page=page,
        ).first()

        if existing:
            # Already saved - redirect to notebook
            return redirect("notebooks:notebook-detail", pk=notebook.pk)

        # Create entry
        entry = NotebookEntry.objects.create(
            notebook=notebook,
            meeting_page=page,
            note=note,
        )

        # Handle existing tags
        if tag_ids:
            tags = Tag.objects.filter(user=request.user, id__in=tag_ids)
            entry.tags.add(*tags)

        # Handle new tag creation
        if new_tag:
            tag, _ = Tag.objects.get_or_create(
                user=request.user,
                name=new_tag,
            )
            entry.tags.add(tag)

        # Update notebook's modified time
        notebook.save()

        # Redirect to notebook detail
        return redirect("notebooks:notebook-detail", pk=notebook.pk)
