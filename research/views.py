import uuid
from datetime import date

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, QuerySet
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.loader import render_to_string
from django.views import View
from django.views.generic import DetailView, ListView

from municipalities.models import Muni
from searches.queryspec import QuerySpec

from .browse import browse_set, set_facets
from .lens import Lens
from .materialize import create_result_set
from .models import Project, ResultSet


def _split_csv(value: str | None) -> tuple[str, ...]:
    return tuple(part.strip() for part in (value or "").split(",") if part.strip())


def _uuid_values(value: str | None) -> tuple[uuid.UUID, ...]:
    values: list[uuid.UUID] = []
    for part in _split_csv(value):
        try:
            values.append(uuid.UUID(part))
        except ValueError:
            continue
    return tuple(values)


def _date_value(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


class ProjectListView(LoginRequiredMixin, ListView):
    model = Project
    template_name = "research/project_list.html"
    context_object_name = "projects"

    def get_queryset(self) -> QuerySet[Project]:
        return Project.objects.filter(owner=self.request.user).annotate(  # type: ignore[misc]
            set_count=Count("sets")
        )


class ProjectDetailView(LoginRequiredMixin, DetailView):
    model = Project
    template_name = "research/project_detail.html"
    context_object_name = "project"
    pk_url_kwarg = "project"

    def get_queryset(self) -> QuerySet[Project]:
        return Project.objects.filter(owner=self.request.user)  # type: ignore[misc]


class SetCreateView(LoginRequiredMixin, View):
    def post(self, request, project):
        project_obj = get_object_or_404(Project, id=project, owner=request.user)
        document_type = (request.POST.get("document_type") or "all").strip()
        if document_type not in {"agenda", "minutes"}:
            document_type = "all"

        spec = QuerySpec(
            text=(request.POST.get("q") or "").strip(),
            meeting_name=(request.POST.get("meeting_name") or "").strip(),
            municipality_ids=_uuid_values(request.POST.get("municipality")),
            states=_split_csv(request.POST.get("states")),
            date_from=_date_value(request.POST.get("date_from")),
            date_to=_date_value(request.POST.get("date_to")),
            document_type=document_type,
        )
        name = (request.POST.get("name") or "").strip() or "Untitled set"

        result_set = create_result_set(project_obj, name, spec)
        return redirect(
            "research:set-detail",
            project=project_obj.id,
            set=result_set.id,
        )


class SetDetailView(LoginRequiredMixin, DetailView):
    model = ResultSet
    template_name = "research/set_detail.html"
    context_object_name = "result_set"
    pk_url_kwarg = "set"

    def get_queryset(self) -> QuerySet[ResultSet]:
        return ResultSet.objects.filter(  # type: ignore[misc]
            project_id=self.kwargs["project"],
            project__owner=self.request.user,
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        result_set = self.object
        lens = Lens.from_querydict(self.request.GET)

        context["lens"] = lens
        context["querystring"] = lens.to_querystring()
        context["items"] = []
        context["next_cursor"] = None
        context["facets"] = {}

        if result_set.status == ResultSet.Status.READY:
            items, next_cursor = browse_set(
                result_set, lens, after=self.request.GET.get("after") or None
            )
            context["items"] = items
            context["next_cursor"] = next_cursor
            context["facets"] = self._municipality_names(set_facets(result_set, lens))

        return context

    @staticmethod
    def _municipality_names(facets: dict) -> dict:
        rows = facets.get("municipalities", [])
        names = dict(
            Muni.objects.filter(
                id__in=[row["municipality_id"] for row in rows]
            ).values_list("id", "name")
        )
        for row in rows:
            row["name"] = names.get(row["municipality_id"], "")
        return facets


class SetStatusView(LoginRequiredMixin, View):
    def get(self, request, project, **kwargs):
        result_set = get_object_or_404(
            ResultSet,
            id=kwargs["set"],
            project_id=project,
            project__owner=request.user,
        )
        html = render_to_string(
            "research/partials/set_status.html",
            {"result_set": result_set},
            request=request,
        )
        return HttpResponse(html)
