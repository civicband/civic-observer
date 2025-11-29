from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, QuerySet
from django.urls import reverse_lazy
from django.views.generic import CreateView, DetailView, ListView

from .forms import NotebookForm
from .models import Notebook


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
