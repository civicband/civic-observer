import uuid
from datetime import date

from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, QuerySet
from django.http import (
    FileResponse,
    Http404,
    HttpResponse,
    HttpResponseBadRequest,
    QueryDict,
    StreamingHttpResponse,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.text import slugify
from django.views import View
from django.views.generic import DetailView, ListView

from meetings.models import MeetingPage
from municipalities.models import Muni
from searches import search_backends
from searches.queryspec import QuerySpec

from .audit import record_event
from .browse import _encode_cursor, browse_set, filter_items, review_item, set_facets
from .codebook import (
    apply_code_to_lens,
    apply_code_to_pages,
    archive_code,
    codebook_tree,
    create_code,
    merge_codes,
    remove_code_from_pages,
    rename_code,
    set_exemplar,
)
from .disposition import dispose_item, dispose_items, undo_disposition_event
from .export import export_zip
from .lens import Lens
from .materialize import create_result_set
from .memos import create_memo, delete_memo, project_timeline, update_memo
from .methods import build_methods
from .models import (
    AuditEvent,
    Code,
    Coding,
    IgnoreRule,
    Memo,
    Project,
    ResultSet,
    SetItem,
)
from .projects import create_project, project_list_for_user
from .rules import is_empty, preview_rule, revert_rule, validate_rule_predicate
from .tasks import apply_rule_to_project_task, export_lens_task, revert_rule_task

MAX_NAME_LENGTH = 200
MAX_REASON_LENGTH = 500


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
        .distinct()
        .order_by("ignore_reason")[:20]
    )


def _enqueue_rule_task(task, rule_id, actor_id=None) -> None:
    import django_rq

    queue = django_rq.get_queue(settings.RESEARCH_QUEUE)
    queue.enqueue(
        task, rule_id, actor_id=actor_id, job_timeout=settings.RESEARCH_JOB_TIMEOUT
    )


def _export_filename(result_set: ResultSet) -> str:
    slug = slugify(result_set.name) or "export"
    return f"{slug}-export.zip"


def _enqueue_export(result_set: ResultSet, lens: Lens, user):
    import django_rq

    queue = django_rq.get_queue(settings.RESEARCH_QUEUE)
    return queue.enqueue(
        export_lens_task,
        result_set.id,
        lens.canonical(),
        user.id,
        job_timeout=settings.RESEARCH_JOB_TIMEOUT,
    )


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


def _spec_from_search(params) -> QuerySpec:
    document_type = (params.get("document_type") or "all").strip()
    if document_type not in {"agenda", "minutes"}:
        document_type = "all"
    return QuerySpec(
        text=(params.get("query") or "").strip(),
        meeting_name=(params.get("meeting_name_query") or "").strip(),
        municipality_ids=_uuid_values(params.get("municipalities")),
        states=_split_csv(params.get("states")),
        date_from=_date_value(params.get("date_from")),
        date_to=_date_value(params.get("date_to")),
        document_type=document_type,
    )


class ProjectCreateView(LoginRequiredMixin, View):
    def post(self, request):
        try:
            project = create_project(
                request.user,
                request.POST.get("name", ""),
                request.POST.get("description", ""),
            )
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))
        return redirect("research:project-detail", project=project.id)


class SaveAsSetView(LoginRequiredMixin, View):
    template_name = "research/partials/save_as_set_panel.html"

    def get(self, request):
        spec = _spec_from_search(request.GET)
        backend = search_backends.get_search_backend()
        count = backend.count_matches(spec, settings.RESEARCH_SET_MAX_ITEMS + 1)
        over_ceiling = count > settings.RESEARCH_SET_MAX_ITEMS
        hidden_params = [
            {"name": key, "value": value}
            for key, values in request.GET.lists()
            for value in values
        ]
        return render(
            request,
            self.template_name,
            {
                "spec": spec,
                "count": count,
                "over_ceiling": over_ceiling,
                "projects": project_list_for_user(request.user),
                "max_items": settings.RESEARCH_SET_MAX_ITEMS,
                "hidden_params": hidden_params,
            },
        )

    def post(self, request):
        spec = _spec_from_search(request.POST)
        project_id = request.POST.get("project")
        new_project = (request.POST.get("new_project") or "").strip()
        name = (request.POST.get("name") or "").strip() or "Untitled set"

        if new_project:
            try:
                project = create_project(request.user, new_project)
            except ValueError as exc:
                return HttpResponseBadRequest(str(exc))
        else:
            project = get_object_or_404(Project, id=project_id, owner=request.user)

        result_set = create_result_set(project, name, spec)
        return redirect("research:set-detail", project=project.id, set=result_set.id)


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

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["timeline"] = project_timeline(self.object)
        return context


class MethodsView(LoginRequiredMixin, View):
    template_name = "research/methods.html"

    def get(self, request, project):
        project_obj = get_object_or_404(Project, id=project, owner=request.user)
        markdown = build_methods(project_obj)
        return render(
            request,
            self.template_name,
            {"project": project_obj, "methods_markdown": markdown},
        )


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
        name = (request.POST.get("name") or "").strip()
        if len(name) > MAX_NAME_LENGTH:
            return HttpResponseBadRequest("Set name is too long")
        name = name or "Untitled set"

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
        context["codes"] = result_set.project.codes.filter(is_archived=False).order_by(
            "name"
        )

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


class ExportView(LoginRequiredMixin, View):
    def get(self, request, project, set):
        result_set = get_object_or_404(
            ResultSet,
            id=set,
            project_id=project,
            project__owner=request.user,
        )
        lens = Lens.from_querydict(request.GET)
        job_id = request.GET.get("job")
        if job_id:
            return self._job_response(request, result_set, job_id)

        count = filter_items(result_set, lens).count()
        if count <= settings.RESEARCH_INLINE_EXPORT_MAX_ROWS:
            payload = export_zip(result_set.project, result_set, lens)
            response = StreamingHttpResponse(
                iter([payload]), content_type="application/zip"
            )
            response["Content-Disposition"] = (
                f'attachment; filename="{_export_filename(result_set)}"'
            )
            return response

        job = _enqueue_export(result_set, lens, request.user)
        return render(
            request,
            "research/export.html",
            {"result_set": result_set, "job_id": job.id, "count": count},
            status=202,
        )

    def _job_response(self, request, result_set, job_id):
        from pathlib import Path

        import django_rq

        queue = django_rq.get_queue(settings.RESEARCH_QUEUE)
        job = queue.fetch_job(job_id)
        if job is None:
            raise Http404("Export job not found")
        if job.func_name != "research.tasks.export_lens_task":
            raise Http404("Export job not found")
        if not job.args or str(job.args[0]) != str(result_set.id):
            raise Http404("Export job not found")

        if job.is_failed:
            return HttpResponse("Export failed", status=500)
        if not job.is_finished:
            return render(
                request,
                "research/export.html",
                {
                    "result_set": result_set,
                    "job_id": job.id,
                    "still_generating": True,
                },
                status=202,
            )

        if not job.result:
            return HttpResponse("Export result not available", status=500)
        export_dir = (Path(settings.MEDIA_ROOT) / "research_exports").resolve()
        result_path = Path(job.result).resolve()
        if not result_path.is_relative_to(export_dir):
            raise Http404("Export result not found")

        return FileResponse(
            open(result_path, "rb"),
            as_attachment=True,
            filename=_export_filename(result_set),
        )


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
        if len(name) > MAX_NAME_LENGTH:
            return HttpResponseBadRequest("Set name is too long")
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
        lens = Lens.from_querydict(QueryDict(request.POST.get("lens", "")))
        code_id = request.POST.get("code")
        if code_id:
            code = get_object_or_404(Code, id=code_id, project=result_set.project)
            if request.POST.get("confirm") != "1":
                count = filter_items(result_set, lens).count()
                return render(
                    request,
                    "research/partials/bulk_confirm.html",
                    {
                        "result_set": result_set,
                        "lens": lens,
                        "querystring": lens.to_querystring(),
                        "code": code,
                        "count": count,
                    },
                )
            apply_code_to_lens(
                result_set.project, code, result_set, lens, actor=request.user
            )
            response = HttpResponse("")
            response["HX-Refresh"] = "true"
            return response
        disposition = request.POST.get("disposition", "")
        if disposition not in SetItem.Disposition.values:
            return HttpResponseBadRequest("Invalid disposition")
        reason = request.POST.get("reason", "")
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

    def _render(self, request, project, initial, preview=None, error="", status=200):
        return render(
            request,
            self.template_name,
            {
                "project": project,
                "initial": initial,
                "preview": preview,
                "error": error,
            },
            status=status,
        )

    def get(self, request, project):
        project_obj = self._project(request, project)
        initial: dict = {}
        page_id = request.GET.get("page")
        lens_param = request.GET.get("lens")
        if page_id:
            try:
                page_pk = int(page_id)
            except (TypeError, ValueError):
                page_pk = None
            if page_pk is not None:
                item = get_object_or_404(
                    SetItem.objects.select_related("page"),
                    id=page_pk,
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

        if is_empty(spec):
            return self._render(
                request,
                project_obj,
                request.POST,
                error="A rule needs at least one condition.",
                status=400,
            )

        predicate_error = validate_rule_predicate(spec)
        if predicate_error:
            return self._render(
                request,
                project_obj,
                request.POST,
                error=predicate_error,
                status=400,
            )

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
        if len(name) > MAX_NAME_LENGTH:
            return self._render(
                request,
                project_obj,
                request.POST,
                error="Rule name is too long",
                status=400,
            )

        reason = (request.POST.get("reason") or "").strip()[:MAX_REASON_LENGTH]
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
        _enqueue_rule_task(apply_rule_to_project_task, rule.id, request.user.id)
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
            _enqueue_rule_task(apply_rule_to_project_task, rule_obj.id, request.user.id)
        else:
            _enqueue_rule_task(revert_rule_task, rule_obj.id, request.user.id)
        return redirect("research:rule-list", project=project_obj.id)


class IgnoreRuleDeleteView(LoginRequiredMixin, View):
    def post(self, request, project, rule):
        project_obj = get_object_or_404(Project, id=project, owner=request.user)
        rule_obj = get_object_or_404(IgnoreRule, id=rule, project=project_obj)
        revert_rule(rule_obj, actor=request.user)
        record_event(
            project_obj,
            "rule.changed",
            actor=request.user,
            payload={"deleted": True, "name": rule_obj.name},
        )
        rule_obj.delete()
        return redirect("research:rule-list", project=project_obj.id)


class CodebookView(LoginRequiredMixin, View):
    template_name = "research/codebook.html"

    def get(self, request, project):
        project_obj = get_object_or_404(Project, id=project, owner=request.user)
        return render(request, self.template_name, _codebook_context(project_obj))


def _codebook_context(project_obj: Project) -> dict:
    return {
        "project": project_obj,
        "tree": codebook_tree(project_obj),
        "include_archived": False,
        "codes": Code.objects.filter(project=project_obj, is_archived=False).order_by(
            "name"
        ),
    }


class CodeCreateView(LoginRequiredMixin, View):
    template_name = "research/codebook.html"

    def _render_error(self, request, project_obj, error):
        context = _codebook_context(project_obj)
        context["error"] = error
        return render(request, self.template_name, context, status=400)

    def post(self, request, project):
        project_obj = get_object_or_404(Project, id=project, owner=request.user)
        parent_id = request.POST.get("parent")
        parent = None
        if parent_id:
            parent = get_object_or_404(Code, id=parent_id, project=project_obj)
        try:
            create_code(
                project_obj,
                name=request.POST.get("name", ""),
                parent=parent,
                color=request.POST.get("color", ""),
                definition=request.POST.get("definition", ""),
                inclusion=request.POST.get("inclusion", ""),
                exclusion=request.POST.get("exclusion", ""),
                actor=request.user,
            )
        except ValueError as exc:
            return self._render_error(request, project_obj, str(exc))
        return redirect("research:codebook", project=project_obj.id)


class CodeRenameView(LoginRequiredMixin, View):
    template_name = "research/codebook.html"

    def _render_error(self, request, project_obj, error):
        context = _codebook_context(project_obj)
        context["error"] = error
        return render(request, self.template_name, context, status=400)

    def post(self, request, project, code):
        code_obj = get_object_or_404(
            Code, id=code, project__owner=request.user, project_id=project
        )
        try:
            rename_code(
                code_obj,
                name=request.POST.get("name"),
                color=request.POST.get("color"),
                definition=request.POST.get("definition"),
                inclusion=request.POST.get("inclusion"),
                exclusion=request.POST.get("exclusion"),
                actor=request.user,
            )
        except ValueError as exc:
            return self._render_error(request, code_obj.project, str(exc))
        return redirect("research:codebook", project=project)


class CodeArchiveView(LoginRequiredMixin, View):
    def post(self, request, project, code):
        code_obj = get_object_or_404(
            Code, id=code, project__owner=request.user, project_id=project
        )
        archive_code(code_obj, actor=request.user)
        return redirect("research:codebook", project=project)


class CodeMergeView(LoginRequiredMixin, View):
    template_name = "research/codebook.html"

    def post(self, request, project, code):
        source = get_object_or_404(
            Code, id=code, project__owner=request.user, project_id=project
        )
        target = get_object_or_404(
            Code, id=request.POST.get("target"), project=source.project
        )
        try:
            merge_codes(source, target, actor=request.user)
        except ValueError as exc:
            context = _codebook_context(source.project)
            context["error"] = str(exc)
            return render(request, self.template_name, context, status=400)
        return redirect("research:codebook", project=project)


def _page_coding_context(project_obj: Project, page: MeetingPage) -> dict:
    set_item = (
        SetItem.objects.filter(result_set__project=project_obj, page=page)
        .select_related("result_set")
        .first()
    )
    codings = list(
        Coding.objects.filter(project=project_obj, page=page).select_related("code")
    )
    for coding in codings:
        coding.changed = bool(
            coding.page_hash
            and page.content_hash
            and coding.page_hash != page.content_hash
        )
    return {
        "project": project_obj,
        "page": page,
        "codes": codings,
        "available_codes": Code.objects.filter(
            project=project_obj, is_archived=False
        ).order_by("name"),
        "memos": Memo.objects.filter(project=project_obj, page=page).order_by(
            "-created"
        ),
        "next": (
            reverse(
                "research:set-detail",
                args=[project_obj.id, set_item.result_set_id],
            )
            if set_item is not None
            else ""
        ),
    }


def _page_in_project(project_obj: Project, page: MeetingPage) -> None:
    if not SetItem.objects.filter(
        result_set__project=project_obj, page_id=page.id
    ).exists():
        raise Http404


class PageCodingView(LoginRequiredMixin, View):
    template_name = "research/partials/page_coding.html"

    def get(self, request, project, page):
        project_obj = get_object_or_404(Project, id=project, owner=request.user)
        page_obj = get_object_or_404(MeetingPage, id=page)
        _page_in_project(project_obj, page_obj)
        return render(
            request, self.template_name, _page_coding_context(project_obj, page_obj)
        )


class PageCodeView(LoginRequiredMixin, View):
    template_name = "research/partials/page_coding.html"

    def post(self, request, project, page):
        project_obj = get_object_or_404(Project, id=project, owner=request.user)
        page_obj = get_object_or_404(MeetingPage, id=page)
        _page_in_project(project_obj, page_obj)
        code_obj = get_object_or_404(
            Code, id=request.POST.get("code"), project=project_obj
        )
        action = request.POST.get("action")
        if action == "apply":
            apply_code_to_pages(
                project_obj, code_obj, [page_obj.id], actor=request.user
            )
        elif action == "remove":
            remove_code_from_pages(
                project_obj, code_obj, [page_obj.id], actor=request.user
            )
        else:
            return HttpResponseBadRequest("Unknown action")
        return render(
            request, self.template_name, _page_coding_context(project_obj, page_obj)
        )


class CodeExemplarView(LoginRequiredMixin, View):
    template_name = "research/partials/page_coding.html"

    def post(self, request, project, code, coding):
        project_obj = get_object_or_404(Project, id=project, owner=request.user)
        coding_obj = get_object_or_404(
            Coding, id=coding, code_id=code, project=project_obj
        )
        set_exemplar(
            coding_obj,
            is_exemplar=request.POST.get("is_exemplar") == "1",
            actor=request.user,
        )
        page_obj = get_object_or_404(MeetingPage, id=coding_obj.page_id)
        return render(
            request, self.template_name, _page_coding_context(project_obj, page_obj)
        )


def _memo_target(model, raw_id, project):
    if not raw_id:
        return None
    return get_object_or_404(model, id=raw_id, project=project)


class MemoCreateView(LoginRequiredMixin, View):
    def post(self, request, project):
        project_obj = get_object_or_404(Project, id=project, owner=request.user)
        result_set = _memo_target(
            ResultSet, request.POST.get("result_set"), project_obj
        )
        code = _memo_target(Code, request.POST.get("code"), project_obj)
        page_id = request.POST.get("page")
        page = None
        if page_id:
            if not SetItem.objects.filter(
                result_set__project=project_obj, page_id=page_id
            ).exists():
                raise Http404
            page = get_object_or_404(MeetingPage, pk=page_id)
        try:
            create_memo(
                project_obj,
                author=request.user,
                body=request.POST.get("body", ""),
                result_set=result_set,
                code=code,
                page=page,
            )
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))
        if result_set is not None:
            return redirect(
                "research:set-detail", project=project_obj.id, set=result_set.id
            )
        if code is not None:
            return redirect("research:codebook", project=project_obj.id)
        next_url = request.POST.get("next")
        if next_url and url_has_allowed_host_and_scheme(
            next_url, allowed_hosts={request.get_host()}
        ):
            return redirect(next_url)
        return redirect("research:project-detail", project=project_obj.id)


class MemoUpdateView(LoginRequiredMixin, View):
    def post(self, request, project, memo):
        memo_obj = get_object_or_404(
            Memo, id=memo, project__owner=request.user, project_id=project
        )
        try:
            update_memo(memo_obj, body=request.POST.get("body", ""))
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))
        return redirect("research:project-detail", project=project)


class MemoDeleteView(LoginRequiredMixin, View):
    def post(self, request, project, memo):
        memo_obj = get_object_or_404(
            Memo, id=memo, project__owner=request.user, project_id=project
        )
        delete_memo(memo_obj)
        return redirect("research:project-detail", project=project)
