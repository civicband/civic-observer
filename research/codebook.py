"""Codebook operations: manage codes, codings, exemplars, and hierarchy."""

from __future__ import annotations

from typing import Any

from django.db import IntegrityError, connection, transaction
from django.db.models import Count, Q
from django.utils import timezone

from meetings.models import MeetingPage

from .audit import record_event
from .browse import filter_items
from .lens import Lens
from .models import Code, Coding, Memo


def codebook_tree(project, *, include_archived: bool = False) -> list[dict]:
    """Return codes as a one-level tree with counts and exemplars."""
    surviving_page_ids = MeetingPage.objects.values("pk")
    codes = Code.objects.filter(project=project).annotate(
        coding_count=Count("codings", filter=Q(codings__page_id__in=surviving_page_ids))
    )
    if not include_archived:
        codes = codes.exclude(is_archived=True)
    ordered_codes = list(codes.order_by("created", "name"))

    nodes: dict[Any, dict[str, Any]] = {}
    for code in ordered_codes:
        nodes[code.id] = {
            "code": code,
            "children": [],
            "coding_count": getattr(code, "coding_count", 0),
            "missing_exclusion": not bool(code.exclusion.strip()),
            "exemplars": list(
                Coding.objects.filter(
                    code=code,
                    is_exemplar=True,
                    page_id__in=surviving_page_ids,
                ).select_related("page")
            ),
        }

    roots: list[dict[str, Any]] = []
    for code in ordered_codes:
        node = nodes[code.id]
        if code.parent_id is None:
            roots.append(node)
        else:
            parent = nodes.get(code.parent_id)
            if parent is not None:
                parent["children"].append(node)

    roots.sort(key=lambda node: (node["code"].created, node["code"].name))
    for node in nodes.values():
        node["children"].sort(
            key=lambda child: (child["code"].created, child["code"].name)
        )

    return roots


def create_code(
    project,
    *,
    name,
    parent=None,
    color="",
    definition="",
    inclusion="",
    exclusion="",
    actor=None,
) -> Code:
    """Create a top-level or one-level-deep code."""
    name = name.strip()
    if not name:
        raise ValueError("A code name is required")
    if len(name) > 100:
        raise ValueError("A code name must be 100 characters or fewer")

    if parent is not None:
        parent = Code.objects.get(pk=parent.pk)
        if parent.project_id != project.id:
            raise ValueError("Parent code belongs to another project")
        if parent.parent_id is not None:
            raise ValueError("Codes can only be nested one level deep")

    try:
        with transaction.atomic():
            code = Code.objects.create(
                project=project,
                name=name,
                parent=parent,
                color=color,
                definition=definition,
                inclusion=inclusion,
                exclusion=exclusion,
            )
            record_event(
                project,
                "code.created",
                actor=actor,
                payload={
                    "name": code.name,
                    "parent": str(parent.id) if parent else None,
                    "color": code.color,
                    "definition": code.definition,
                    "inclusion": code.inclusion,
                    "exclusion": code.exclusion,
                },
            )
    except IntegrityError as exc:
        raise ValueError("A code with that name already exists") from exc

    return code


def rename_code(
    code,
    *,
    name=None,
    color=None,
    definition=None,
    inclusion=None,
    exclusion=None,
    actor=None,
) -> Code:
    """Update the provided fields and record before/after values."""
    passed = {
        "name": name,
        "color": color,
        "definition": definition,
        "inclusion": inclusion,
        "exclusion": exclusion,
    }
    if name is not None:
        name = name.strip()
        if not name:
            raise ValueError("A code name is required")
        passed["name"] = name
    changed = {
        field: value
        for field, value in passed.items()
        if value is not None and value != getattr(code, field)
    }
    if not changed:
        return code

    before = {field: getattr(code, field) for field in changed}
    for field, value in changed.items():
        setattr(code, field, value)
    after = {field: getattr(code, field) for field in changed}

    try:
        with transaction.atomic():
            code.save(update_fields=[*changed, "modified"])
            record_event(
                code.project,
                "code.changed",
                actor=actor,
                payload={"before": before, "after": after},
            )
    except IntegrityError as exc:
        raise ValueError("A code with that name already exists") from exc
    return code


def archive_code(code, *, actor=None) -> Code:
    """Archive a code and its direct children."""
    with transaction.atomic():
        code.is_archived = True
        code.save(update_fields=["is_archived", "modified"])

        children = list(Code.objects.filter(parent=code))
        Code.objects.filter(parent=code).update(
            is_archived=True, modified=timezone.now()
        )

        record_event(
            code.project,
            "code.archived",
            actor=actor,
            payload={
                "name": code.name,
                "children": [str(child.id) for child in children],
            },
        )
    return code


def merge_codes(source, target, *, actor=None) -> int:
    """Move codings from source to target, then delete the source code."""
    source = Code.objects.get(pk=source.pk)
    target = Code.objects.get(pk=target.pk)

    if source.id == target.id:
        raise ValueError("Cannot merge a code into itself")
    if source.project_id != target.project_id:
        raise ValueError("Cannot merge codes from different projects")
    if Code.objects.filter(parent=source).exists():
        raise ValueError("Archive the source code's children before merging")

    source_id = source.id
    target_id = target.id
    source_name = source.name
    target_name = target.name
    project = source.project

    sql = """
        INSERT INTO research_coding
            (id, created, modified, project_id, code_id, page_id,
             span_start, span_end, quote, page_hash, is_exemplar, coder_id)
        SELECT gen_random_uuid(), now(), now(), project_id, %s, page_id,
               span_start, span_end, quote, page_hash, is_exemplar, coder_id
        FROM research_coding
        WHERE code_id = %s
        ON CONFLICT DO NOTHING
    """

    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute(sql, [target_id, source_id])
            moved = cursor.rowcount

        Memo.objects.filter(code=source).update(code=target)
        Coding.objects.filter(code=source).delete()
        source.delete()

        record_event(
            project,
            "code.merged",
            actor=actor,
            payload={
                "source": str(source_id),
                "target": str(target_id),
                "source_name": source_name,
                "target_name": target_name,
                "moved": moved,
            },
        )

    return moved


def apply_code_to_pages(
    project,
    code,
    page_ids,
    *,
    actor=None,
    result_set=None,
    lens: Lens | None = None,
) -> list[Coding]:
    """Apply a code to pages, skipping pages that are already coded."""
    code = Code.objects.get(pk=code.pk)
    if code.project_id != project.id:
        raise ValueError("Code belongs to another project")

    page_ids = list(dict.fromkeys(page_ids))
    pages = MeetingPage.objects.in_bulk(page_ids)
    if not pages:
        return []

    relevant_page_ids = list(pages.keys())
    before_ids = set(
        Coding.objects.filter(code=code, page_id__in=relevant_page_ids).values_list(
            "id", flat=True
        )
    )

    rows = [
        Coding(
            project=project,
            code=code,
            page=page,
            quote=page.text,
            page_hash=page.content_hash or "",
            coder=actor,
        )
        for page in pages.values()
    ]

    with transaction.atomic():
        Coding.objects.bulk_create(rows, ignore_conflicts=True)

        new_codings = [
            coding
            for coding in Coding.objects.filter(
                code=code, page_id__in=relevant_page_ids
            )
            if coding.id not in before_ids
        ]

        if not new_codings:
            return []

        payload: dict[str, Any] = {
            "code": str(code.id),
            "count": len(new_codings),
            "page_ids": [page.id for page in pages.values()][:100],
        }
        if lens is not None:
            payload["lens"] = lens.canonical()
        record_event(
            project,
            "items.coded",
            actor=actor,
            result_set=result_set,
            payload=payload,
        )

    return new_codings


def apply_code_to_lens(
    project,
    code,
    result_set,
    lens: Lens,
    *,
    actor=None,
    item_ids: list[int] | None = None,
) -> list[Coding]:
    """Apply a code to every page visible through a lens.

    When ``item_ids`` is provided, only those items are updated.
    """
    items = filter_items(result_set, lens)
    if item_ids:
        items = items.filter(id__in=item_ids)
    page_ids = list(items.values_list("page_id", flat=True))
    return apply_code_to_pages(
        project,
        code,
        page_ids,
        actor=actor,
        result_set=result_set,
        lens=lens,
    )


def remove_code_from_pages(
    project,
    code,
    page_ids,
    *,
    actor=None,
    result_set=None,
    lens: Lens | None = None,
) -> int:
    """Remove a code from pages and record an uncoded event."""
    page_ids = [str(page_id) for page_id in page_ids]
    with transaction.atomic():
        deleted, _ = Coding.objects.filter(
            project=project, code=code, page_id__in=page_ids
        ).delete()

        if deleted == 0:
            return 0

        payload: dict[str, Any] = {
            "code": str(code.id),
            "count": deleted,
            "page_ids": page_ids[:100],
        }
        if lens is not None:
            payload["lens"] = lens.canonical()
        record_event(
            project,
            "items.uncoded",
            actor=actor,
            result_set=result_set,
            payload=payload,
        )

    return deleted


def remove_code_from_lens(project, code, result_set, lens: Lens, *, actor=None) -> int:
    """Remove a code from every page visible through a lens."""
    page_ids = list(filter_items(result_set, lens).values_list("page_id", flat=True))
    return remove_code_from_pages(
        project,
        code,
        page_ids,
        actor=actor,
        result_set=result_set,
        lens=lens,
    )


def set_exemplar(coding, *, is_exemplar: bool = True, actor=None) -> Coding:
    """Mark or unmark a coding as an exemplar."""
    before = coding.is_exemplar
    if before == is_exemplar:
        return coding

    coding.is_exemplar = is_exemplar
    coding.save(update_fields=["is_exemplar", "modified"])
    record_event(
        coding.project,
        "code.changed",
        actor=actor,
        payload={
            "code": str(coding.code_id),
            "before": {"is_exemplar": before},
            "after": {"is_exemplar": coding.is_exemplar},
        },
    )
    return coding
