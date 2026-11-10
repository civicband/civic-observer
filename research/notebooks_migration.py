"""Migrate Notebooks data into Research Projects (Release 1)."""

from __future__ import annotations

from typing import Any

from django.db import transaction
from django.utils import timezone

SAVED_PAGES = "Saved pages"


def _models(apps) -> dict[str, Any]:
    """Return historical model classes for the migration to use.

    The live ``notebooks`` models were removed in Release 2, so this helper is
    only usable from the historical migration state.
    """
    if apps is None:
        raise RuntimeError("Notebook migration requires historical apps")

    return {
        "Project": apps.get_model("research", "Project"),
        "ResultSet": apps.get_model("research", "ResultSet"),
        "SetItem": apps.get_model("research", "SetItem"),
        "Memo": apps.get_model("research", "Memo"),
        "Code": apps.get_model("research", "Code"),
        "Coding": apps.get_model("research", "Coding"),
        "AuditEvent": apps.get_model("research", "AuditEvent"),
        "Notebook": apps.get_model("notebooks", "Notebook"),
        "Tag": apps.get_model("notebooks", "Tag"),
    }


def _record_event(models, project, verb, *, actor=None, result_set=None, payload=None):
    return models["AuditEvent"].objects.create(
        project=project,
        actor=actor,
        verb=verb,
        result_set=result_set,
        payload=payload or {},
    )


def _project_for_notebook(notebook, models):
    project, _created = models["Project"].objects.get_or_create(
        id=notebook.id,
        defaults={
            "owner": notebook.user,
            "name": notebook.name,
            "is_archived": notebook.is_archived,
        },
    )
    return project


def _saved_pages_set(project, models):
    result_set, _created = models["ResultSet"].objects.get_or_create(
        project=project,
        name=SAVED_PAGES,
        origin="import",
        defaults={
            "status": "ready",
            "materialized_at": timezone.now(),
        },
    )
    return result_set


def _migrate_entries(project, result_set, notebook, models):
    counts: dict[str, Any] = {
        "entries": 0,
        "notes": 0,
        "codes": set(),
        "codings": 0,
    }
    SetItem = models["SetItem"]
    Memo = models["Memo"]
    Code = models["Code"]
    Coding = models["Coding"]

    for entry in notebook.entries.select_related("meeting_page").prefetch_related(
        "tags"
    ):
        page = entry.meeting_page
        _item, created = SetItem.objects.get_or_create(
            result_set=result_set,
            page=page,
            defaults={
                "disposition": "r",
                "added_via": "import",
                "added_at": entry.created,
                "state": page.state,
                "document_type": page.document_type,
                "meeting_date": page.meeting_date,
                "municipality_id": page.municipality_id,
            },
        )
        if created:
            counts["entries"] += 1

        note = (entry.note or "").strip()
        if note:
            _memo, memo_created = Memo.objects.get_or_create(
                project=project,
                page=page,
                author=notebook.user,
                body=note,
            )
            if memo_created:
                counts["notes"] += 1

        for tag in entry.tags.all():
            code, _code_created = Code.objects.get_or_create(
                project=project,
                parent=None,
                name=tag.name,
            )
            _coding, coding_created = Coding.objects.get_or_create(
                project=project,
                code=code,
                page=page,
                span_start=None,
                span_end=None,
                defaults={"coder": notebook.user},
            )
            if coding_created:
                counts["codes"].add(code.id)
                counts["codings"] += 1
    return counts


def _code_orphan_tags(user_id, models) -> dict[str, int]:
    """Create codes for a user's tags used nowhere, once per user.

    ``user_id=None`` tags cannot be given an owner-scoped code, so they are
    reported instead of silently dropped while the notebooks tables remain.
    """
    Tag = models["Tag"]
    orphan_tags = list(Tag.objects.filter(user_id=user_id, entries__isnull=True))
    if not orphan_tags:
        return {"tags": 0, "codes": 0, "skipped": 0}
    if user_id is None:
        return {"tags": len(orphan_tags), "codes": 0, "skipped": len(orphan_tags)}

    Project = models["Project"]
    Code = models["Code"]
    project = (
        Project.objects.filter(owner_id=user_id, is_archived=False)
        .order_by("-modified")
        .first()
    )
    if project is None:
        project = Project.objects.create(owner_id=user_id, name="My research")

    created = 0
    for tag in orphan_tags:
        _code, was_created = Code.objects.get_or_create(
            project=project, parent=None, name=tag.name
        )
        if was_created:
            created += 1
    return {"tags": len(orphan_tags), "codes": created, "skipped": 0}


@transaction.atomic
def migrate_notebook(notebook, models) -> dict:
    project = _project_for_notebook(notebook, models)
    result_set = _saved_pages_set(project, models)
    counts = _migrate_entries(project, result_set, notebook, models)
    result_set.item_count = (
        models["SetItem"].objects.filter(result_set=result_set).count()
    )
    result_set.save(update_fields=["item_count", "modified"])

    if not project.events.filter(
        verb="project.imported", payload__notebook=str(notebook.id)
    ).exists():
        _record_event(
            models,
            project,
            "project.imported",
            actor=notebook.user,
            result_set=result_set,
            payload={
                "notebook": str(notebook.id),
                "entries": counts["entries"],
                "notes": counts["notes"],
                "codes": len(counts["codes"]),
                "codings": counts["codings"],
            },
        )
    return {
        "project": project,
        "set": result_set,
        "entries": counts["entries"],
        "notes": counts["notes"],
        "codes": len(counts["codes"]),
        "codings": counts["codings"],
    }


def migrate_notebooks(notebooks=None, apps=None) -> dict[str, dict]:
    models = _models(apps)
    Notebook = models["Notebook"]
    if notebooks is None:
        notebooks = Notebook.objects.select_related("user").all()
    results = {
        str(notebook.id): migrate_notebook(notebook, models=models)
        for notebook in notebooks
    }

    Tag = models["Tag"]
    user_ids = list(
        Tag.objects.filter(entries__isnull=True)
        .order_by()
        .values_list("user_id", flat=True)
        .distinct()
    )
    for user_id in user_ids:
        _code_orphan_tags(user_id, models)
    return results


def unmigrate_notebooks(notebook_ids=None, apps=None) -> int:
    models = _models(apps)
    Notebook = models["Notebook"]
    if notebook_ids is None:
        notebook_ids = Notebook.objects.values_list("id", flat=True)
    deleted, _details = (
        models["Project"].objects.filter(id__in=list(notebook_ids)).delete()
    )
    return deleted
