"""Project creation helpers for the search entry point."""

from __future__ import annotations

from .models import Project


def create_project(user, name: str, description: str = "") -> Project:
    cleaned = (name or "").strip()
    if not cleaned:
        raise ValueError("A project needs a name")
    if len(cleaned) > 200:
        raise ValueError("A project name must be 200 characters or fewer")
    return Project.objects.create(
        owner=user, name=cleaned, description=(description or "").strip()
    )


def project_list_for_user(user):
    return Project.objects.filter(owner=user, is_archived=False).order_by("-modified")
