"""Migrate Notebooks data into Research Projects (Release 1)."""

from __future__ import annotations

from typing import Any

from django.db import transaction
from django.utils import timezone

from .audit import record_event
from .models import Code, Coding, Memo, Project, ResultSet, SetItem

SAVED_PAGES = "Saved pages"


def _project_for_notebook(notebook):
    project, _created = Project.objects.get_or_create(
        id=notebook.id,
        defaults={
            "owner": notebook.user,
            "name": notebook.name,
            "is_archived": notebook.is_archived,
        },
    )
    return project


def _saved_pages_set(project):
    result_set, _created = ResultSet.objects.get_or_create(
        project=project,
        name=SAVED_PAGES,
        origin=ResultSet.Origin.IMPORT,
        defaults={
            "status": ResultSet.Status.READY,
            "materialized_at": timezone.now(),
        },
    )
    return result_set


def _migrate_entries(project, result_set, notebook):
    counts: dict[str, Any] = {
        "entries": 0,
        "notes": 0,
        "codes": set(),
        "codings": 0,
    }
    for entry in notebook.entries.select_related("meeting_page").prefetch_related(
        "tags"
    ):
        page = entry.meeting_page
        _item, created = SetItem.objects.get_or_create(
            result_set=result_set,
            page=page,
            defaults={
                "disposition": SetItem.Disposition.RELEVANT,
                "added_via": SetItem.AddedVia.IMPORT,
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


def _code_orphan_tags(user):
    """Create codes for tags used nowhere in the user's most recent project."""
    from notebooks.models import Tag

    orphan_tags = Tag.objects.filter(user=user, entries__isnull=True)
    if not orphan_tags.exists():
        return 0
    project = (
        Project.objects.filter(owner=user, is_archived=False)
        .order_by("-modified")
        .first()
    )
    if project is None:
        project = Project.objects.create(owner=user, name="My research")
    created = 0
    for tag in orphan_tags:
        _code, was_created = Code.objects.get_or_create(
            project=project, parent=None, name=tag.name
        )
        if was_created:
            created += 1
    return created


@transaction.atomic
def migrate_notebook(notebook) -> dict:
    project = _project_for_notebook(notebook)
    result_set = _saved_pages_set(project)
    counts = _migrate_entries(project, result_set, notebook)
    result_set.item_count = SetItem.objects.filter(result_set=result_set).count()
    result_set.save(update_fields=["item_count", "modified"])

    if not project.events.filter(
        verb="project.imported", payload__notebook=str(notebook.id)
    ).exists():
        record_event(
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
    _code_orphan_tags(notebook.user)
    return {
        "project": project,
        "set": result_set,
        "entries": counts["entries"],
        "notes": counts["notes"],
        "codes": len(counts["codes"]),
        "codings": counts["codings"],
    }


def migrate_notebooks(notebooks=None) -> dict[str, dict]:
    from notebooks.models import Notebook

    if notebooks is None:
        notebooks = Notebook.objects.select_related("user").all()
    return {str(notebook.id): migrate_notebook(notebook) for notebook in notebooks}


def unmigrate_notebooks(notebook_ids=None) -> int:
    from notebooks.models import Notebook

    if notebook_ids is None:
        notebook_ids = Notebook.objects.values_list("id", flat=True)
    deleted, _details = Project.objects.filter(id__in=list(notebook_ids)).delete()
    return deleted
