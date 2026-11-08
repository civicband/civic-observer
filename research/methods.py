from __future__ import annotations

from typing import Any

from django.db.models import Count, Max

from searches.queryspec import QuerySpec

from .models import Project, ResultSet

_METHOD_EVENT_VERBS = ("set.materialized", "set.new_matches", "set.derived")


def build_methods(project: Project) -> str:
    lines: list[str] = [f"# How we did this — {project.name}", ""]
    lines.extend(_queries_section(project))
    lines.extend(_funnel_section(project))
    lines.extend(_codebook_section(project))
    lines.extend(_last_checked_section(project))
    return "\n".join(lines).strip() + "\n"


def _queries_section(project: Project) -> list[str]:
    lines = ["## Queries"]
    events = project.events.filter(verb__in=_METHOD_EVENT_VERBS).order_by("created")
    for event in events:
        payload = event.payload or {}
        if event.verb == "set.materialized":
            spec = QuerySpec.from_canonical(payload.get("query", {}))
            lines.append(
                f"- {_describe_spec(spec)} ({event.created:%Y-%m-%d})"
                f" — {payload.get('item_count', 0)} matches"
            )
        elif event.verb == "set.new_matches":
            lines.append(
                f"- Checked for new matches on {event.created:%Y-%m-%d}"
                f" (cutoff {payload.get('cutoff', '')}):"
                f" {payload.get('added', 0)} added"
            )
        elif event.verb == "set.derived":
            lines.append(
                f"- Frozen lens {_describe_lens(payload.get('lens', {}))}"
                f" ({event.created:%Y-%m-%d}) — {payload.get('item_count', 0)} items"
            )
    lines.append("")
    return lines


def _funnel_section(project: Project) -> list[str]:
    lines = ["## Funnel"]
    for result_set in project.sets.order_by("created"):
        lines.extend(_funnel_for_set(result_set))
    lines.append("")
    return lines


def _funnel_for_set(result_set: ResultSet) -> list[str]:
    items = result_set.items
    ignored_by_rule = (
        items.filter(disposition="i", disposition_by_rule__isnull=False)
        .values("disposition_by_rule__name")
        .annotate(count=Count("id"))
        .order_by("disposition_by_rule__name")
    )

    lines = [f"### {result_set.name}", f"- Matched: {result_set.item_count}"]
    for row in ignored_by_rule:
        name = row["disposition_by_rule__name"]
        lines.append(f"- Ignored by rule {name}: {row['count']}")
    lines.append(
        f"- Ignored by hand: "
        f"{items.filter(disposition='i', disposition_by_rule__isnull=True).count()}"
    )
    lines.append(f"- Relevant: {items.filter(disposition='r').count()}")
    lines.append(f"- Unreviewed: {items.filter(disposition='u').count()}")
    lines.append("")
    return lines


def _codebook_section(project: Project) -> list[str]:
    lines = ["## Codebook"]
    for code in project.codes.filter(is_archived=False).order_by("name"):
        lines.extend(
            [
                f"### {code.name}",
                f"- Definition: {code.definition or '—'}",
                f"- Apply when: {code.inclusion or '—'}",
                f"- Do not apply when: {code.exclusion or '—'}",
                f"- Codings: {code.codings.count()}",
                "",
            ]
        )
    return lines


def _last_checked_section(project: Project) -> list[str]:
    last_checked = project.sets.aggregate(last=Max("last_checked_at"))["last"]
    value = f"{last_checked:%Y-%m-%d}" if last_checked else "never"
    return ["## Last checked", f"Last checked for new matches: {value}", ""]


def _describe_spec(spec: QuerySpec) -> str:
    parts: list[str] = []
    if spec.text:
        parts.append(f'text "{spec.text}"')
    if spec.meeting_name:
        parts.append(f'meeting "{spec.meeting_name}"')
    if spec.states:
        parts.append(f"states {', '.join(spec.states)}")
    if spec.municipality_ids:
        parts.append(f"municipalities: {len(spec.municipality_ids)}")
    if spec.date_from:
        parts.append(f"from {spec.date_from.isoformat()}")
    if spec.date_to:
        parts.append(f"to {spec.date_to.isoformat()}")
    if spec.document_type and spec.document_type != "all":
        parts.append(f"type {spec.document_type}")
    return "; ".join(parts) or "No conditions"


def _describe_lens(lens: dict[str, Any]) -> str:
    parts: list[str] = []
    if lens.get("text"):
        parts.append(f'text "{lens["text"]}"')
    states = lens.get("states") or []
    if states:
        parts.append(f"states {', '.join(states)}")
    municipality_ids = lens.get("municipality_ids") or []
    if municipality_ids:
        parts.append(f"municipalities: {len(municipality_ids)}")
    if lens.get("date_from"):
        parts.append(f"from {lens['date_from']}")
    if lens.get("date_to"):
        parts.append(f"to {lens['date_to']}")
    document_type = lens.get("document_type")
    if document_type and document_type != "all":
        parts.append(f"type {document_type}")
    return "; ".join(parts) or "No conditions"
