"""Add single pages to research sets (the "Add to set" entry point)."""

from __future__ import annotations

from django.utils import timezone

from .audit import record_event
from .models import Code, Coding, Memo, Project, ResultSet, SetItem


def default_set_for_user(user) -> ResultSet:
    result_set = (
        ResultSet.objects.filter(project__owner=user, project__is_archived=False)
        .order_by("-modified")
        .first()
    )
    if result_set is not None:
        return result_set
    project = Project.objects.create(owner=user, name="My research")
    return ResultSet.objects.create(
        project=project,
        name="Saved pages",
        origin=ResultSet.Origin.MANUAL,
        status=ResultSet.Status.READY,
        materialized_at=timezone.now(),
    )


def add_page_to_set(
    project,
    result_set,
    page,
    *,
    actor,
    note: str = "",
    code_names: list[str] | None = None,
) -> SetItem:
    if result_set.project_id != project.id:
        raise ValueError("The set belongs to another project")
    item, created = SetItem.objects.get_or_create(
        result_set=result_set,
        page=page,
        defaults={
            "disposition": SetItem.Disposition.UNREVIEWED,
            "added_via": SetItem.AddedVia.MANUAL,
            "added_at": timezone.now(),
            "state": page.state,
            "document_type": page.document_type,
            "meeting_date": page.meeting_date,
            "municipality_id": page.municipality_id,
        },
    )
    if created:
        record_event(
            project,
            "item.added",
            actor=actor,
            result_set=result_set,
            payload={"page": page.id, "added_via": "manual"},
        )
        result_set.item_count = SetItem.objects.filter(result_set=result_set).count()
        result_set.save(update_fields=["item_count", "modified"])

    body = (note or "").strip()
    if body:
        Memo.objects.get_or_create(project=project, page=page, author=actor, body=body)
    for name in code_names or []:
        cleaned = name.strip()
        if not cleaned:
            continue
        code, _created = Code.objects.get_or_create(
            project=project, parent=None, name=cleaned
        )
        Coding.objects.get_or_create(
            project=project,
            code=code,
            page=page,
            span_start=None,
            span_end=None,
            defaults={"coder": actor},
        )
    return item


def add_page_to_default_set(
    user, page, *, actor, note: str = "", code_names: list[str] | None = None
) -> SetItem:
    result_set = default_set_for_user(user)
    return add_page_to_set(
        result_set.project,
        result_set,
        page,
        actor=actor,
        note=note,
        code_names=code_names,
    )
