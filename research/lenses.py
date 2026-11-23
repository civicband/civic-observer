"""Lenses: named, reusable views of a set or of the whole project."""

from __future__ import annotations

from dataclasses import replace

from django.db import IntegrityError, transaction

from .audit import record_event
from .lens import Lens
from .models import Lens as LensModel
from .models import Project, ResultSet

MAX_NAME_LENGTH = 200


def _clean_name(name: str | None) -> str:
    cleaned = (name or "").strip()
    if not cleaned:
        raise ValueError("A Lens needs a name")
    if len(cleaned) > MAX_NAME_LENGTH:
        raise ValueError("A Lens name must be 200 characters or fewer")
    return cleaned


def _validate_scope(project: Project, result_set: ResultSet | None) -> None:
    if result_set is not None and result_set.project_id != project.id:
        raise ValueError("The set belongs to another project")


def lenses_for(
    project: Project, *, result_set: ResultSet | None = None
) -> list[LensModel]:
    """Saved Lenses for one scope, ordered by name."""
    return list(
        project.lenses.filter(result_set=result_set).order_by("name", "created")
    )


def save_lens(
    project: Project,
    *,
    name: str,
    lens: Lens,
    result_set: ResultSet | None = None,
    actor=None,
) -> LensModel:
    """Create a named Lens for a set or the project."""
    _validate_scope(project, result_set)
    cleaned = _clean_name(name)
    try:
        with transaction.atomic():
            lens_obj = LensModel.objects.create(
                project=project,
                result_set=result_set,
                name=cleaned,
                conditions=lens.canonical(),
            )
            record_event(
                project,
                "lens.saved",
                actor=actor,
                payload={
                    "name": lens_obj.name,
                    "scope": lens_obj.scope_label,
                    "conditions": lens_obj.conditions,
                    "result_set": str(result_set.id) if result_set else None,
                },
            )
    except IntegrityError as exc:
        raise ValueError("A Lens with that name already exists") from exc
    return lens_obj


def update_lens(
    lens_obj: LensModel,
    *,
    name: str | None = None,
    lens: Lens | None = None,
    actor=None,
) -> LensModel:
    """Rename a Lens and/or replace its conditions."""
    changed: dict = {}
    if name is not None:
        cleaned = _clean_name(name)
        if cleaned != lens_obj.name:
            changed["name"] = cleaned
    if lens is not None:
        canonical = lens.canonical()
        if canonical != lens_obj.conditions:
            changed["conditions"] = canonical
    if not changed:
        return lens_obj

    before = {field: getattr(lens_obj, field) for field in changed}
    for field, value in changed.items():
        setattr(lens_obj, field, value)
    try:
        with transaction.atomic():
            lens_obj.save(update_fields=[*changed, "modified"])
            record_event(
                lens_obj.project,
                "lens.changed",
                actor=actor,
                payload={
                    "name": lens_obj.name,
                    "before": before,
                    "after": changed,
                },
            )
    except IntegrityError as exc:
        raise ValueError("A Lens with that name already exists") from exc
    return lens_obj


def delete_lens(lens_obj: LensModel, *, actor=None) -> None:
    project = lens_obj.project
    payload = {"name": lens_obj.name, "scope": lens_obj.scope_label}
    lens_obj.delete()
    record_event(project, "lens.deleted", actor=actor, payload=payload)


def promote_lens(lens_obj: LensModel, *, name: str | None = None, actor=None):
    """Freeze a Lens into a new result set and remember the created set."""
    from .freeze import freeze_lens
    from .project_pages import freeze_project_lens

    target_name = _clean_name(name if name is not None else lens_obj.name)
    conditions = Lens.from_canonical(lens_obj.conditions or {})
    if lens_obj.result_set_id is None:
        created = freeze_project_lens(
            lens_obj.project, target_name, conditions, actor=actor
        )
    else:
        parent = lens_obj.result_set
        assert parent is not None
        created = freeze_lens(
            parent, target_name, conditions, copy_dispositions=True
        )
    lens_obj.promoted_set = created
    lens_obj.save(update_fields=["promoted_set", "modified"])
    record_event(
        lens_obj.project,
        "lens.promoted",
        actor=actor,
        result_set=created,
        payload={
            "name": lens_obj.name,
            "scope": lens_obj.scope_label,
            "set": str(created.id),
        },
    )
    return created


def _lens_from_querystring(querystring: str) -> Lens:
    from urllib.parse import parse_qs

    return Lens.from_querydict(parse_qs(querystring))


def lens_from_querystring(querystring: str | None) -> Lens:
    if not querystring:
        return Lens()
    return _lens_from_querystring(querystring)


def active_lens(
    project: Project,
    params,
    *,
    result_set: ResultSet | None = None,
) -> tuple[LensModel | None, Lens]:
    """Resolve the active Lens for a scope.

    A saved Lens wins: when ``?lens=<uuid>`` names an in-scope Lens, its stored
    conditions are applied and any other URL filters are ignored. Otherwise the
    URL filter parameters are parsed as an ad-hoc (unsaved) Lens.
    """
    lens_id = params.get("lens") if hasattr(params, "get") else None
    if lens_id:
        lens_obj = (
            project.lenses.filter(result_set=result_set, id=lens_id).first()
            if _looks_like_uuid(lens_id)
            else None
        )
        if lens_obj is not None:
            return lens_obj, Lens.from_canonical(lens_obj.conditions or {})
    return None, Lens.from_querydict(params)


def _looks_like_uuid(value) -> bool:
    import uuid

    try:
        uuid.UUID(str(value))
    except (TypeError, ValueError):
        return False
    return True


def review_lens(params) -> tuple[Lens, bool]:
    """Parse review filters, defaulting to unreviewed-only."""
    lens = Lens.from_querydict(params)
    if "disposition" not in params:
        lens = replace(lens, dispositions=("u",))
    return lens, set(lens.dispositions) == {"u", "r"}
