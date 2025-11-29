from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, QuerySet
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.loader import render_to_string
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import (
    CreateView,
    DeleteView,
    DetailView,
    ListView,
    UpdateView,
)

from meetings.models import MeetingPage

from .forms import NotebookEntryForm, NotebookForm
from .models import Notebook, NotebookEntry


class NotebookListView(LoginRequiredMixin, ListView):
    model = Notebook
    template_name = "notebooks/notebook_list.html"
    context_object_name = "notebooks"

    def get_queryset(self) -> QuerySet[Notebook]:
        # LoginRequiredMixin ensures user is authenticated
        qs = Notebook.objects.filter(user=self.request.user).annotate(  # type: ignore[misc]
            entry_count=Count("entries")
        )

        if not self.request.GET.get("show_archived"):
            qs = qs.filter(is_archived=False)

        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["show_archived"] = bool(self.request.GET.get("show_archived"))
        return context


class NotebookCreateView(LoginRequiredMixin, CreateView):
    model = Notebook
    form_class = NotebookForm
    template_name = "notebooks/notebook_form.html"
    success_url = reverse_lazy("notebooks:notebook-list")

    def form_valid(self, form):
        form.instance.user = self.request.user
        return super().form_valid(form)


class NotebookDetailView(LoginRequiredMixin, DetailView):
    model = Notebook
    template_name = "notebooks/notebook_detail.html"
    context_object_name = "notebook"

    def get_queryset(self) -> QuerySet[Notebook]:
        return Notebook.objects.filter(user=self.request.user).prefetch_related(  # type: ignore[misc]
            "entries__meeting_page__document__municipality",
            "entries__tags",
        )


class NotebookEditView(LoginRequiredMixin, UpdateView):
    model = Notebook
    form_class = NotebookForm
    template_name = "notebooks/notebook_form.html"
    success_url = reverse_lazy("notebooks:notebook-list")

    def get_queryset(self) -> QuerySet[Notebook]:
        return Notebook.objects.filter(user=self.request.user)  # type: ignore[misc]


class NotebookArchiveView(LoginRequiredMixin, View):
    def post(self, request, pk):
        notebook = get_object_or_404(Notebook, pk=pk, user=request.user)
        notebook.is_archived = not notebook.is_archived
        notebook.save()
        return redirect("notebooks:notebook-list")


class NotebookDeleteView(LoginRequiredMixin, DeleteView):  # type: ignore[misc]
    model = Notebook
    template_name = "notebooks/notebook_confirm_delete.html"
    success_url = reverse_lazy("notebooks:notebook-list")

    def get_queryset(self) -> QuerySet[Notebook]:
        return Notebook.objects.filter(user=self.request.user)  # type: ignore[misc]


class SavePageView(LoginRequiredMixin, View):
    """HTMX endpoint to save a meeting page to a notebook."""

    def post(self, request):
        page_id = request.POST.get("page_id")
        notebook_id = request.POST.get("notebook_id")

        # Get the page
        page = get_object_or_404(MeetingPage, id=page_id)

        # Get or create target notebook
        if notebook_id:
            notebook = get_object_or_404(Notebook, id=notebook_id, user=request.user)
        else:
            # Use most recently modified notebook, or create one
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

        # Check if already saved
        existing = NotebookEntry.objects.filter(
            notebook=notebook,
            meeting_page=page,
        ).first()

        if existing:
            html = render_to_string(
                "notebooks/partials/toast.html",
                {
                    "message": f"Already in {notebook.name}",
                    "type": "info",
                    "page_id": page_id,
                    "is_saved": True,
                },
                request=request,
            )
            return HttpResponse(html)

        # Create entry
        NotebookEntry.objects.create(
            notebook=notebook,
            meeting_page=page,
        )

        # Update notebook's modified time
        notebook.save()

        html = render_to_string(
            "notebooks/partials/toast.html",
            {
                "message": f"Saved to {notebook.name}",
                "type": "success",
                "page_id": page_id,
                "is_saved": True,
                "notebook": notebook,
                "notebooks": Notebook.objects.filter(
                    user=request.user, is_archived=False
                ).exclude(id=notebook.id),
            },
            request=request,
        )
        return HttpResponse(html)


class EntryEditView(LoginRequiredMixin, UpdateView):
    model = NotebookEntry
    form_class = NotebookEntryForm
    template_name = "notebooks/entry_form.html"
    pk_url_kwarg = "entry_pk"

    def get_queryset(self) -> QuerySet[NotebookEntry]:
        return NotebookEntry.objects.filter(  # type: ignore[misc]
            notebook__user=self.request.user,
            notebook_id=self.kwargs["pk"],
        )

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_success_url(self):
        return reverse_lazy(
            "notebooks:notebook-detail", kwargs={"pk": self.kwargs["pk"]}
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["notebook"] = get_object_or_404(
            Notebook, pk=self.kwargs["pk"], user=self.request.user
        )
        return context


class EntryDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk, entry_pk):
        entry = get_object_or_404(
            NotebookEntry,
            pk=entry_pk,
            notebook_id=pk,
            notebook__user=request.user,
        )
        entry.delete()
        return redirect("notebooks:notebook-detail", pk=pk)
