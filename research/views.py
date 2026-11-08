import uuid
from datetime import date

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, QuerySet
from django.http import HttpResponse, HttpResponseBadRequest, QueryDict
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.views import View
from django.views.generic import DetailView, ListView

from meetings.models import MeetingPage
from municipalities.models import Muni
from searches.queryspec import QuerySpec

from .browse import browse_set, filter_items, set_facets
from .disposition import dispose_item, dispose_items, undo_disposition_event
from .lens import Lens
from .materialize import create_result_set
from .models import AuditEvent, Project, ResultSet, SetItem


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


def _undo_single_item_id(event: AuditEvent) -> int | None:
    payload = event.payload or {}
    if payload.get("count") != 1:
        return None
    item_ids = payload.get("item_ids")
    if not item_ids:
        return None
    return int(item_ids[0])


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
            after = self.request.GET.get("after") or None
            try:
                items, next_cursor = browse_set(result_set, lens, after=after)
            except ValueError:
                items, next_cursor = browse_set(result_set, lens, after=None)
            context["items"] = items
            context["next_cursor"] = next_cursor
            context["facets"] = self._municipality_names(set_facets(result_set, lens))
            context["lens_count"] = filter_items(result_set, lens).count()

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
            {
                "result_set": result_set,
                "querystring": Lens.from_querydict(request.GET).to_querystring(),
            },
            request=request,
        )
        response = HttpResponse(html)
        if result_set.status == ResultSet.Status.READY:
            response["HX-Refresh"] = "true"
        return response


class DispositionView(LoginRequiredMixin, View):
    def post(self, request, project, set, item):
        result_set = get_object_or_404(
            ResultSet,
            id=set,
            project_id=project,
            project__owner=request.user,
        )
        set_item = get_object_or_404(
            SetItem.objects.select_related("last_event"),
            id=item,
            result_set=result_set,
        )
        disposition = request.POST.get("disposition", "")
        reason = request.POST.get("reason", "")
        lens = Lens.from_querydict(QueryDict(request.POST.get("lens", "")))
        try:
            dispose_item(set_item, disposition, actor=request.user, reason=reason)
        except ValueError:
            return HttpResponseBadRequest("Invalid disposition")

        set_item.refresh_from_db()
        set_item.page_obj = MeetingPage.objects.in_bulk([set_item.page_id]).get(  # type: ignore[attr-defined]
            set_item.page_id
        )
        context = {
            "result_set": result_set,
            "lens": lens,
            "querystring": lens.to_querystring(),
            "item": set_item,
        }
        return render(request, "research/partials/item.html", context)


class BulkActionView(LoginRequiredMixin, View):
    def post(self, request, project, set):
        result_set = get_object_or_404(
            ResultSet,
            id=set,
            project_id=project,
            project__owner=request.user,
        )
        if result_set.status != ResultSet.Status.READY:
            return HttpResponse("Set is not ready", status=409)
        disposition = request.POST.get("disposition", "")
        if disposition not in SetItem.Disposition.values:
            return HttpResponseBadRequest("Invalid disposition")
        reason = request.POST.get("reason", "")
        lens = Lens.from_querydict(QueryDict(request.POST.get("lens", "")))
        if request.POST.get("confirm") != "1":
            count = filter_items(result_set, lens).count()
            return render(
                request,
                "research/partials/bulk_confirm.html",
                {
                    "result_set": result_set,
                    "lens": lens,
                    "querystring": lens.to_querystring(),
                    "disposition": disposition,
                    "disposition_label": SetItem.Disposition(disposition).label,
                    "reason": reason,
                    "count": count,
                },
            )
        dispose_items(result_set, lens, disposition, actor=request.user, reason=reason)
        response = HttpResponse("")
        response["HX-Refresh"] = "true"
        return response


class UndoView(LoginRequiredMixin, View):
    def post(self, request, project, set, event):
        result_set = get_object_or_404(
            ResultSet,
            id=set,
            project_id=project,
            project__owner=request.user,
        )
        audit_event = get_object_or_404(
            AuditEvent,
            id=event,
            project=result_set.project,
            result_set=result_set,
        )
        try:
            undo_event, count = undo_disposition_event(audit_event, actor=request.user)
        except ValueError:
            return HttpResponseBadRequest("Event cannot be undone")
        if count == 0:
            return HttpResponse("Nothing to undo", status=409)

        item_id = _undo_single_item_id(audit_event)
        if item_id is None:
            response = HttpResponse("")
            response["HX-Refresh"] = "true"
            return response

        set_item = get_object_or_404(
            SetItem.objects.select_related("last_event"),
            id=item_id,
            result_set=result_set,
        )
        set_item.page_obj = MeetingPage.objects.in_bulk([set_item.page_id]).get(  # type: ignore[attr-defined]
            set_item.page_id
        )
        lens = Lens.from_querydict(QueryDict(request.POST.get("lens", "")))
        context = {
            "result_set": result_set,
            "lens": lens,
            "querystring": lens.to_querystring(),
            "item": set_item,
        }
        return render(request, "research/partials/item.html", context)
