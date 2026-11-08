"""Append-only audit events for research decisions."""

from __future__ import annotations

from typing import Any

from .models import AuditEvent, Project, ResultSet


def record_event(
    project: Project,
    verb: str,
    *,
    actor=None,
    result_set: ResultSet | None = None,
    payload: dict[str, Any] | None = None,
) -> AuditEvent:
    """Write one append-only event and return it."""
    return AuditEvent.objects.create(
        project=project,
        actor=actor,
        verb=verb,
        result_set=result_set,
        payload=payload or {},
    )
