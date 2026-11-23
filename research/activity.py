"""Project activity feed: presenters, filters, rollups, and the feed builder."""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date

from django.db.models import Count
from django.db.models.functions import TruncDate
from django.urls import reverse

from meetings.models import MeetingPage

from .models import AuditEvent, Code, IgnoreRule, Project, ResultSet

# Verbs grouped for filtering / rollups.
GROUP_OF_VERB = {
    "set.materialized": "sets",
    "set.new_matches": "sets",
    "set.derived": "sets",
    "set.created": "sets",
    "items.disposed": "review",
    "bulk.undone": "review",
    "item.added": "review",
    "items.coded": "codes",
    "items.uncoded": "codes",
    "code.created": "codes",
    "code.changed": "codes",
    "code.archived": "codes",
    "code.merged": "codes",
    "rule.created": "rules",
    "rule.changed": "rules",
    "rule.applied": "rules",
    "rule.reverted": "rules",
    "lens.saved": "lenses",
    "lens.changed": "lenses",
    "lens.deleted": "lenses",
    "lens.promoted": "lenses",
    "project.imported": "other",
}

GROUP_LABELS = {
    "sets": "Sets",
    "review": "Review",
    "codes": "Codes",
    "rules": "Rules",
    "lenses": "Lenses",
    "other": "Other",
}

DISPOSITION_LABELS = {"r": "Relevant", "i": "Ignored", "u": "Unreviewed"}


def verbs_for_group(group: str) -> list[str]:
    return [verb for verb, value in GROUP_OF_VERB.items() if value == group]


def _to_uuid(value) -> uuid.UUID | None:
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


class ActivityContext:
    """Bulk-resolved names and pages so presenters never query per row."""

    def __init__(self, project: Project):
        self.project = project
        self.set_names: dict[uuid.UUID, str] = {}
        self.pages: dict[str, MeetingPage] = {}
        self.codes: dict[uuid.UUID, str] = {}
        self.code_by_name: dict[str, uuid.UUID] = {}
        self.rules: dict[uuid.UUID, IgnoreRule] = {}

    @classmethod
    def load(cls, project: Project, events) -> ActivityContext:
        ctx = cls(project)
        set_ids: set[uuid.UUID] = set()
        code_ids: set[uuid.UUID] = set()
        page_ids: set[str] = set()
        rule_names: set[str] = set()

        for event in events:
            payload = event.payload or {}
            if event.result_set_id:
                set_ids.add(event.result_set_id)
            for set_id in (payload.get("counts") or {}):
                parsed = _to_uuid(set_id)
                if parsed:
                    set_ids.add(parsed)
            for key in ("code",):
                parsed = _to_uuid(payload.get(key))
                if parsed:
                    code_ids.add(parsed)
            if event.verb in {"code.created", "code.changed", "code.merged"}:
                parsed = _to_uuid(payload.get("target"))
                if parsed:
                    code_ids.add(parsed)
            for key in ("page_ids", "pages"):
                for page_id in payload.get(key) or []:
                    page_ids.add(str(page_id))
            if payload.get("page"):
                page_ids.add(str(payload["page"]))
            if event.verb in {"rule.created", "rule.changed"}:
                name = payload.get("name")
                if isinstance(name, str) and name:
                    rule_names.add(name)

        if set_ids:
            ctx.set_names = {
                result_set.id: result_set.name
                for result_set in ResultSet.objects.filter(
                    project=project, id__in=set_ids
                ).only("id", "name")
            }
        if code_ids:
            for code in Code.objects.filter(project=project, id__in=code_ids).only(
                "id", "name"
            ):
                ctx.codes[code.id] = code.name
        if rule_names:
            for rule in IgnoreRule.objects.filter(project=project, name__in=rule_names):
                ctx.rules[rule.id] = rule
        if page_ids:
            ctx.pages = MeetingPage.objects.in_bulk(list(page_ids))
        return ctx

    def set_name(self, set_id) -> str:
        parsed = _to_uuid(set_id)
        if parsed is not None and parsed in self.set_names:
            return self.set_names[parsed]
        return "a set"

    def code_name(self, code_id) -> str | None:
        parsed = _to_uuid(code_id)
        if parsed is None:
            return None
        return self.codes.get(parsed)

    def page(self, page_id) -> MeetingPage | None:
        return self.pages.get(str(page_id))


def _set_link(project: Project, set_id, *, querystring: str = "") -> str | None:
    parsed = _to_uuid(set_id)
    if parsed is None:
        return None
    url = reverse("research:set-detail", args=[project.id, parsed])
    return f"{url}?{querystring}" if querystring else url


def _project_pages_link(project: Project, querystring: str = "") -> str:
    url = reverse("research:project-pages", args=[project.id])
    return f"{url}?{querystring}" if querystring else url


def _code_link(project: Project, code_id, *, dispositions: str = "u,r,i") -> str | None:
    parsed = _to_uuid(code_id)
    if parsed is None:
        return None
    return _project_pages_link(
        project, f"code_any={parsed}&disposition={dispositions}"
    )


def _page_links(project: Project, ctx: ActivityContext, page_ids, *, limit=5) -> list:
    links = []
    for page_id in list(page_ids or [])[:limit]:
        page = ctx.page(page_id)
        if page is None:
            continue
        links.append(
            {
                "label": f"Page {page.page_number}",
                "href": page.civic_band_url or "",
                "external": True,
            }
        )
    return links


def _ignored_rule_rollup(ignored_by_rule, ctx: ActivityContext) -> list[str]:
    parts = []
    for set_id, count in (ignored_by_rule or {}).items():
        if count:
            parts.append(f"{count} in {ctx.set_name(set_id)}")
    return parts


def describe_event(
    event: AuditEvent, ctx: ActivityContext
) -> dict:
    """Build a structured presentation for one event (no queries)."""
    project = ctx.project
    payload = event.payload or {}
    verb = event.verb
    group = GROUP_OF_VERB.get(verb, "other")

    title = verb.replace(".", " ").title()
    summary = ""
    links: list[dict] = []
    counts: list[dict] = []

    if verb == "items.disposed":
        to = payload.get("to", "")
        title = f"Marked {payload.get('count', 1)} page(s) {DISPOSITION_LABELS.get(to, to)}"
        reason = payload.get("reason", "")
        if reason:
            summary = f"Reason: {reason}"
        item_ids = payload.get("item_ids") or []
        if payload.get("count") == 1 and item_ids:
            page = ctx.page(item_ids[0])
            if page is not None:
                links.extend(_page_links(project, ctx, [item_ids[0]]))
        count = payload.get("count", 1)
        counts.append(
            {
                "label": "in set",
                "href": _set_link(project, event.result_set_id, querystring=f"disposition={to}")
                or "",
                "note": ctx.set_name(event.result_set_id),
            }
        )
        if count and not links:
            summary = summary or f"{count} in {ctx.set_name(event.result_set_id)}"

    elif verb == "bulk.undone":
        title = f"Undid {payload.get('count', 0)} disposition change(s)"
        counts.append(
            {
                "label": "in set",
                "href": _set_link(project, event.result_set_id) or "",
                "note": ctx.set_name(event.result_set_id),
            }
        )

    elif verb == "set.materialized":
        title = f"Built set {ctx.set_name(event.result_set_id)}"
        summary = f"{payload.get('item_count', 0)} page(s)"
        ignored = _ignored_rule_rollup(payload.get("ignored_by_rule"), ctx)
        if ignored:
            summary += " · ignored by rule: " + "; ".join(ignored)
        links.append(
            {"label": "Open set", "href": _set_link(project, event.result_set_id) or ""}
        )

    elif verb == "set.new_matches":
        added = payload.get("added", 0)
        title = f"Checked for new matches in {ctx.set_name(event.result_set_id)}"
        summary = f"{added} added"
        ignored = _ignored_rule_rollup(payload.get("ignored_by_rule"), ctx)
        if ignored:
            summary += " · ignored by rule: " + "; ".join(ignored)
        links.append(
            {"label": "Open set", "href": _set_link(project, event.result_set_id) or ""}
        )

    elif verb == "set.derived":
        parent_id = payload.get("parent")
        title = f"Created set {ctx.set_name(event.result_set_id)} from a Lens"
        summary = f"{payload.get('item_count', 0)} page(s)"
        links.append(
            {"label": "Open set", "href": _set_link(project, event.result_set_id) or ""}
        )
        parent_link = _set_link(project, parent_id)
        if parent_link:
            links.append(
                {"label": f"From {ctx.set_name(parent_id)}", "href": parent_link}
            )

    elif verb in {"rule.applied", "rule.reverted"}:
        action = "Applied" if verb == "rule.applied" else "Reverted"
        title = f"{action} rule"
        summary = f"{payload.get('count', 0)} page(s)"
        for set_id, count in (payload.get("counts") or {}).items():
            if not count:
                continue
            counts.append(
                {
                    "label": f"{ctx.set_name(set_id)}: {count}",
                    "href": _set_link(project, set_id, querystring="disposition=i")
                    or "",
                }
            )

    elif verb in {"rule.created", "rule.changed"}:
        name = payload.get("name", "rule")
        if payload.get("deleted"):
            title = f"Deleted rule '{name}'"
        else:
            title = f"{'Created' if verb == 'rule.created' else 'Changed'} rule '{name}'"
        if payload.get("reason"):
            summary = f"Reason: {payload['reason']}"

    elif verb == "code.created":
        title = f"Created code '{payload.get('name', '')}'"
    elif verb == "code.changed":
        title = "Changed a code"
        changed = payload.get("after") or {}
        name = changed.get("name") or (payload.get("before") or {}).get("name")
        if name:
            summary = f"Code: {name}"
    elif verb == "code.archived":
        title = f"Archived code '{payload.get('name', '')}'"
    elif verb == "code.merged":
        title = (
            f"Merged code '{payload.get('source_name', '')}' into "
            f"'{payload.get('target_name', '')}'"
        )
        summary = f"{payload.get('moved', 0)} coding(s) moved"
        target_link = _code_link(project, payload.get("target"))
        if target_link:
            links.append({"label": "View coded pages", "href": target_link})

    elif verb in {"items.coded", "items.uncoded"}:
        action = "Coded" if verb == "items.coded" else "Removed code from"
        code_name = ctx.code_name(payload.get("code")) or "pages"
        title = f"{action} {payload.get('count', 0)} page(s) with '{code_name}'"
        code_link = _code_link(project, payload.get("code"))
        if code_link:
            links.append({"label": "View coded pages", "href": code_link})
        if event.result_set_id:
            links.append(
                {
                    "label": "Open set",
                    "href": _set_link(project, event.result_set_id) or "",
                }
            )

    elif verb == "item.added":
        title = "Added a page to a set"
        links.extend(_page_links(project, ctx, [payload.get("page")]))
        set_link = _set_link(project, event.result_set_id)
        if set_link:
            links.append({"label": "Open set", "href": set_link})

    elif verb.startswith("lens."):
        name = payload.get("name", "a Lens")
        if verb == "lens.promoted":
            title = f"Promoted Lens '{name}' to a set"
            set_link = _set_link(project, payload.get("set"))
            if set_link:
                links.append({"label": "Open set", "href": set_link})
        elif verb == "lens.deleted":
            title = f"Deleted Lens '{name}'"
        else:
            title = f"{'Saved' if verb == 'lens.saved' else 'Changed'} Lens '{name}'"

    elif verb == "project.imported":
        title = "Imported a project"

    else:
        title = verb.replace(".", " ").capitalize()
        summary = ""

    return {
        "verb": verb,
        "group": group,
        "title": title,
        "summary": summary,
        "links": [link for link in links if link.get("href")],
        "counts": [count for count in counts if count.get("href")],
    }


def _filter_events(
    events,
    *,
    group: str | None,
    result_set_id,
    code_id,
    actor_id,
    date_from: date | None,
    date_to: date | None,
):
    if group:
        verbs = verbs_for_group(group)
        events = events.filter(verb__in=verbs)
    parsed_set = _to_uuid(result_set_id)
    if parsed_set is not None:
        events = events.filter(result_set_id=parsed_set)
    parsed_code = _to_uuid(code_id)
    if parsed_code is not None:
        events = events.filter(payload__code=str(parsed_code))
    if actor_id:
        events = events.filter(actor_id=actor_id)
    if date_from:
        events = events.filter(created__date__gte=date_from)
    if date_to:
        events = events.filter(created__date__lte=date_to)
    return events


def _filter_memos(
    memos,
    *,
    group: str | None,
    result_set_id,
    code_id,
    actor_id,
    date_from: date | None,
    date_to: date | None,
):
    if group and group != "review":
        # Memos are not part of other groups.
        memos = memos.none()
    parsed_set = _to_uuid(result_set_id)
    if parsed_set is not None:
        memos = memos.filter(result_set_id=parsed_set)
    parsed_code = _to_uuid(code_id)
    if parsed_code is not None:
        memos = memos.filter(code_id=parsed_code)
    if actor_id:
        memos = memos.filter(author_id=actor_id)
    if date_from:
        memos = memos.filter(created__date__gte=date_from)
    if date_to:
        memos = memos.filter(created__date__lte=date_to)
    return memos


def build_feed(
    project: Project,
    *,
    group: str | None = None,
    result_set_id=None,
    code_id=None,
    actor_id=None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = 200,
) -> dict:
    """Merge memos and events into a filtered, presented feed."""
    memos = _filter_memos(
        project.memos.select_related("author", "result_set", "code", "page"),
        group=group,
        result_set_id=result_set_id,
        code_id=code_id,
        actor_id=actor_id,
        date_from=date_from,
        date_to=date_to,
    ).order_by("-created")[:limit]
    events = list(
        _filter_events(
            project.events.select_related("actor", "result_set"),
            group=group,
            result_set_id=result_set_id,
            code_id=code_id,
            actor_id=actor_id,
            date_from=date_from,
            date_to=date_to,
        ).order_by("-created")[:limit]
    )
    ctx = ActivityContext.load(project, events)

    entries: list[dict] = [
        {"kind": "memo", "created": memo.created, "memo": memo} for memo in memos
    ]
    for event in events:
        entries.append(
            {
                "kind": "event",
                "created": event.created,
                "event": event,
                "presented": describe_event(event, ctx),
            }
        )
    entries.sort(key=lambda entry: entry["created"], reverse=True)
    truncated = len(entries) > limit
    entries = entries[:limit]
    return {"entries": entries, "truncated": truncated}


def activity_summary(
    project: Project,
    *,
    result_set_id=None,
    code_id=None,
    actor_id=None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict:
    """Rollups: counts per group and per verb over the filtered window."""
    events = _filter_events(
        project.events.all(),
        group=None,
        result_set_id=result_set_id,
        code_id=code_id,
        actor_id=actor_id,
        date_from=date_from,
        date_to=date_to,
    )
    by_verb: dict[str, int] = {}
    for row in events.values("verb").annotate(count=Count("id")):
        by_verb[row["verb"]] = row["count"]

    by_group: dict[str, int] = defaultdict(int)
    for verb, count in by_verb.items():
        by_group[GROUP_OF_VERB.get(verb, "other")] += count

    by_day = []
    for row in (
        events.annotate(day=TruncDate("created"))
        .values("day")
        .annotate(count=Count("id"))
        .order_by("-day")[:30]
    ):
        if row["day"] is not None:
            by_day.append({"day": row["day"], "count": row["count"]})

    return {
        "by_verb": by_verb,
        "by_group": dict(by_group),
        "by_day": by_day,
    }


__all__ = [
    "GROUP_LABELS",
    "GROUP_OF_VERB",
    "ActivityContext",
    "activity_summary",
    "build_feed",
    "describe_event",
    "verbs_for_group",
]
