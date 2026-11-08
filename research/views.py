import uuid
from datetime import date

from django.conf import settings
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

from .audit import record_event
from .browse import _encode_cursor, browse_set, filter_items, review_item, set_facets
from .disposition import dispose_item, dispose_items, undo_disposition_event
from .lens import Lens
from .materialize import create_result_set
from .models import AuditEvent, IgnoreRule, Project, ResultSet, SetItem
from .rules import preview_rule, revert_rule


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


def _spec_from_rule_form(post) -> QuerySpec:
    document_type = (post.get("document_type") or "all").strip()
    if document_type not in {"agenda", "minutes"}:
        document_type = "all"
    return QuerySpec(
        text=(post.get("text") or "").strip(),
        meeting_name=(post.get("meeting_name") or "").strip(),
        municipality_ids=_uuid_values(post.get("municipality")),
        states=_split_csv(post.get("states")),
        date_from=_date_value(post.get("date_from")),
        date_to=_date_value(post.get("date_to")),
        document_type=document_type,
    )


def _ignore_reasons(result_set: ResultSet) -> list[str]:
    return list(
        SetItem.objects.filter(result_set__project=result_set.project)
        .exclude(ignore_reason="")
        .values_list("ignore_reason", flat=True)
        .distinct()[:20]
    )


def _enqueue_rule_task(task, rule_id) -> None:
    import django_rq

    queue = django_rq.get_queue(settings.RESEARCH_QUEUE)
    queue.enqueue(task, rule_id, job_timeout=settings.RESEARCH_JOB_TIMEOUT)


def _rule_form_initial(spec: QuerySpec) -> dict:
    return {
        "text": spec.text,
        "meeting_name": spec.meeting_name,
        "municipality": ",".join(str(value) for value in spec.municipality_ids),
        "states": ",".join(spec.states),
        "date_from": spec.date_from.isoformat() if spec.date_from else "",
        "date_to": spec.date_to.isoformat() if spec.date_to else "",
        "document_type": spec.document_type,
    }


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
        context["ignore_reasons"] = _ignore_reasons(result_set)

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


class FreezeLensView(LoginRequiredMixin, View):
    def post(self, request, project, set):
        parent = get_object_or_404(
            ResultSet, id=set, project_id=project, project__owner=request.user
        )
        lens = Lens.from_querydict(QueryDict(request.POST.get("lens", "")))
        name = (request.POST.get("name") or "").strip()
        if not name:
            return HttpResponseBadRequest("A name is required")
        copy_dispositions = request.POST.get("copy_dispositions") == "1"
        from .freeze import freeze_lens

        child = freeze_lens(parent, name, lens, copy_dispositions=copy_dispositions)
        return redirect("research:set-detail", project=parent.project_id, set=child.id)


class NewMatchesView(LoginRequiredMixin, View):
    def post(self, request, project, set):
        result_set = get_object_or_404(
            ResultSet, id=set, project_id=project, project__owner=request.user
        )
        from .new_matches import create_new_matches

        try:
            create_new_matches(result_set, actor=request.user)
        except ValueError:
            return HttpResponse("Only query sets can check for new matches", status=409)
        if not request.headers.get("HX-Request"):
            return redirect(
                "research:set-detail",
                project=result_set.project_id,
                set=result_set.id,
            )
        response = HttpResponse("")
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
            "ignore_reasons": _ignore_reasons(result_set),
        }
        if request.POST.get("next") == "review":
            try:
                next_item, cursor = review_item(
                    result_set, lens, after=_encode_cursor(set_item, lens.order)
                )
            except ValueError:
                next_item, cursor = review_item(result_set, lens)
            context["item"] = next_item
            context["cursor"] = cursor
            return render(request, "research/partials/review_item.html", context)
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
            "ignore_reasons": _ignore_reasons(result_set),
        }
        if request.POST.get("next") == "review":
            context["cursor"] = _encode_cursor(set_item, lens.order)
            return render(request, "research/partials/review_item.html", context)
        return render(request, "research/partials/item.html", context)


class ReviewView(LoginRequiredMixin, View):
    def get(self, request, project, set):
        result_set = get_object_or_404(
            ResultSet,
            id=set,
            project_id=project,
            project__owner=request.user,
        )
        lens = Lens.from_querydict(request.GET)
        after = request.GET.get("after") or None
        before = request.GET.get("before") or None
        try:
            item, cursor = review_item(result_set, lens, after=after, before=before)
        except ValueError:
            item, cursor = review_item(result_set, lens)
        return render(
            request,
            "research/review.html",
            {
                "result_set": result_set,
                "lens": lens,
                "querystring": lens.to_querystring(),
                "item": item,
                "cursor": cursor,
                "ignore_reasons": _ignore_reasons(result_set),
            },
        )


class IgnoreRuleListView(LoginRequiredMixin, ListView):
    model = IgnoreRule
    template_name = "research/rules/list.html"
    context_object_name = "rules"

    def get_queryset(self) -> QuerySet[IgnoreRule]:
        self.project = get_object_or_404(
            Project, id=self.kwargs["project"], owner=self.request.user
        )
        return IgnoreRule.objects.filter(project=self.project).order_by("-created")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        rows = []
        for rule in context["rules"]:
            spec = QuerySpec.from_canonical(rule.predicate or {})
            rows.append(
                {
                    "rule": rule,
                    "summary": self._summary(spec),
                    "preview_fields": _rule_form_initial(spec),
                }
            )
        context["project"] = self.project
        context["rule_rows"] = rows
        return context

    @staticmethod
    def _summary(spec: QuerySpec) -> str:
        parts: list[str] = []
        if spec.text:
            parts.append(f"text: {spec.text}")
        if spec.meeting_name:
            parts.append(f"meeting: {spec.meeting_name}")
        if spec.municipality_ids:
            parts.append(f"municipalities: {len(spec.municipality_ids)}")
        if spec.states:
            parts.append(f"states: {', '.join(spec.states)}")
        if spec.date_from:
            parts.append(f"from {spec.date_from.isoformat()}")
        if spec.date_to:
            parts.append(f"to {spec.date_to.isoformat()}")
        if spec.document_type and spec.document_type != "all":
            parts.append(f"type: {spec.document_type}")
        return " · ".join(parts) or "No conditions"


class IgnoreRuleCreateView(LoginRequiredMixin, View):
    template_name = "research/rules/form.html"

    def _project(self, request, project) -> Project:
        return get_object_or_404(Project, id=project, owner=request.user)

    def _render(self, request, project, initial, preview=None, error=""):
        return render(
            request,
            self.template_name,
            {
                "project": project,
                "initial": initial,
                "preview": preview,
                "error": error,
            },
        )

    def get(self, request, project):
        project_obj = self._project(request, project)
        initial: dict = {}
        page_id = request.GET.get("page")
        lens_param = request.GET.get("lens")
        if page_id:
            item = get_object_or_404(
                SetItem.objects.select_related("page"),
                id=page_id,
                result_set__project=project_obj,
            )
            initial["meeting_name"] = item.page.meeting_name
            initial["document_type"] = item.page.document_type
        elif lens_param:
            lens = Lens.from_querydict(QueryDict(lens_param))
            initial.update(
                {
                    "text": lens.text,
                    "municipality": ",".join(
                        str(value) for value in lens.municipality_ids
                    ),
                    "states": ",".join(lens.states),
                    "date_from": (lens.date_from.isoformat() if lens.date_from else ""),
                    "date_to": lens.date_to.isoformat() if lens.date_to else "",
                    "document_type": lens.document_type,
                }
            )
        return self._render(request, project_obj, initial)

    def post(self, request, project):
        project_obj = self._project(request, project)
        spec = _spec_from_rule_form(request.POST)
        action = request.POST.get("action")

        if action == "preview":
            preview = preview_rule(project_obj, spec)
            return self._render(request, project_obj, request.POST, preview=preview)

        if action != "save":
            return HttpResponseBadRequest("Unknown action")

        name = (request.POST.get("name") or "").strip()
        if not name:
            return self._render(
                request,
                project_obj,
                request.POST,
                error="A name is required",
            )

        reason = (request.POST.get("reason") or "").strip()
        rule = IgnoreRule.objects.create(
            project=project_obj,
            name=name,
            predicate=spec.canonical(),
            reason=reason,
        )
        record_event(
            project_obj,
            "rule.created",
            actor=request.user,
            payload={"name": rule.name, "predicate": rule.predicate, "reason": reason},
        )
        from .tasks import apply_rule_to_project_task

        _enqueue_rule_task(apply_rule_to_project_task, rule.id)
        return redirect("research:rule-list", project=project_obj.id)


class IgnoreRuleToggleView(LoginRequiredMixin, View):
    def post(self, request, project, rule):
        project_obj = get_object_or_404(Project, id=project, owner=request.user)
        rule_obj = get_object_or_404(IgnoreRule, id=rule, project=project_obj)
        before = rule_obj.is_active
        rule_obj.is_active = not before
        rule_obj.save(update_fields=["is_active", "modified"])
        record_event(
            project_obj,
            "rule.changed",
            actor=request.user,
            payload={
                "before": before,
                "after": rule_obj.is_active,
                "name": rule_obj.name,
            },
        )
        if rule_obj.is_active:
            from .tasks import apply_rule_to_project_task

            _enqueue_rule_task(apply_rule_to_project_task, rule_obj.id)
        else:
            from .tasks import revert_rule_task

            _enqueue_rule_task(revert_rule_task, rule_obj.id)
        return redirect("research:rule-list", project=project_obj.id)


class IgnoreRuleDeleteView(LoginRequiredMixin, View):
    def post(self, request, project, rule):
        project_obj = get_object_or_404(Project, id=project, owner=request.user)
        rule_obj = get_object_or_404(IgnoreRule, id=rule, project=project_obj)
        if rule_obj.is_active:
            revert_rule(rule_obj, actor=request.user)
        record_event(
            project_obj,
            "rule.changed",
            actor=request.user,
            payload={"deleted": True, "name": rule_obj.name},
        )
        rule_obj.delete()
        return redirect("research:rule-list", project=project_obj.id)
