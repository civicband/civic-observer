"""Project-wide ignore rules over materialized set items."""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.db import DatabaseError, connection, transaction
from django.utils import timezone

from searches.queryspec import QuerySpec, build_where

from .audit import record_event
from .models import AuditEvent, IgnoreRule, ResultSet

RULE_APPLIED = "rule.applied"
RULE_REVERTED = "rule.reverted"


def _rule_spec(rule: IgnoreRule) -> QuerySpec:
    return QuerySpec.from_canonical(rule.predicate or {})


def _counts_sql(spec: QuerySpec) -> tuple[str, list[Any]]:
    where, params = build_where(spec, alias="mp")
    return where, params


def is_empty(spec: QuerySpec) -> bool:
    """An empty predicate matches every page; treating it as a rule is unsafe."""
    return not (
        spec.text
        or spec.meeting_name
        or spec.municipality_ids
        or spec.states
        or spec.date_from
        or spec.date_to
        or (spec.document_type and spec.document_type != "all")
        or spec.modified_after
    )


def validate_rule_predicate(spec: QuerySpec) -> str | None:
    """Return a database error message if ``spec`` cannot be executed."""
    if is_empty(spec):
        return None
    where, params = build_where(spec, alias="mp")
    try:
        with transaction.atomic(), connection.cursor() as cur:
            cur.execute(
                f"SELECT 1 FROM meetings_meetingpage mp WHERE {where} LIMIT 1",
                params,
            )
    except DatabaseError as exc:
        return str(exc)
    return None


def preview_rule(project, spec: QuerySpec, *, sample_limit: int = 10) -> dict:
    """Dry-run a rule: per-set unreviewed counts and a small sample."""
    if is_empty(spec):
        return {"counts": [], "total": 0, "samples": []}
    where, params = _counts_sql(spec)
    counts: list[dict] = []
    samples: list[dict] = []
    try:
        with transaction.atomic(), connection.cursor() as cur:
            cur.execute(
                f"""
                SELECT r.id, r.name, count(i.id)
                FROM research_resultset r
                JOIN research_setitem i ON i.result_set_id = r.id
                JOIN meetings_meetingpage mp ON mp.id = i.page_id
                WHERE r.project_id = %s
                  AND i.disposition = 'u'
                  AND {where}
                GROUP BY r.id, r.name
                ORDER BY r.created
                """,
                [project.id, *params],
            )
            counts = [
                {"result_set_id": row[0], "name": row[1], "count": row[2]}
                for row in cur.fetchall()
            ]

            cur.execute(
                f"""
                SELECT i.id, r.name, mp.meeting_name, mp.meeting_date,
                       mp.document_type, mp.page_number, left(mp.text, 200)
                FROM research_setitem i
                JOIN research_resultset r ON r.id = i.result_set_id
                JOIN meetings_meetingpage mp ON mp.id = i.page_id
                WHERE r.project_id = %s
                  AND i.disposition = 'u'
                  AND {where}
                ORDER BY r.created, i.id
                LIMIT %s
                """,
                [project.id, *params, sample_limit],
            )
            samples = [
                {
                    "item_id": row[0],
                    "set_name": row[1],
                    "meeting_name": row[2],
                    "meeting_date": row[3],
                    "document_type": row[4],
                    "page_number": row[5],
                    "text": row[6],
                }
                for row in cur.fetchall()
            ]
    except DatabaseError as exc:
        return {"counts": [], "total": 0, "samples": [], "error": str(exc)}

    return {
        "counts": counts,
        "total": sum(row["count"] for row in counts),
        "samples": samples,
    }


def apply_rule_to_project(rule: IgnoreRule, *, actor=None) -> AuditEvent | None:
    """Apply one active rule across every set in the project."""
    with transaction.atomic():
        locked_rule = IgnoreRule.objects.select_for_update().filter(pk=rule.pk).first()
        if locked_rule is None or not locked_rule.is_active:
            return None
        spec = _rule_spec(locked_rule)
        if is_empty(spec):
            return None
        where, params = _counts_sql(spec)

        event = AuditEvent.objects.create(
            project=locked_rule.project,
            actor=actor,
            verb=RULE_APPLIED,
            payload={
                "predicate": locked_rule.predicate,
                "reason": locked_rule.reason,
                "counts": {},
                "count": 0,
            },
        )
        with connection.cursor() as cur:
            cur.execute(
                "SELECT set_config('statement_timeout', %s, true)",
                [str(settings.RESEARCH_STATEMENT_TIMEOUT_MS)],
            )
            cur.execute(
                f"""
                WITH updated AS (
                    UPDATE research_setitem i
                    SET disposition = 'i',
                        disposition_by_rule_id = %s,
                        ignore_reason = %s,
                        last_event_id = %s
                    FROM meetings_meetingpage mp
                    WHERE i.page_id = mp.id
                      AND i.result_set_id IN (
                          SELECT id FROM research_resultset WHERE project_id = %s
                      )
                      AND i.disposition = 'u'
                      AND {where}
                    RETURNING i.result_set_id
                )
                SELECT result_set_id, count(*)
                FROM updated
                GROUP BY result_set_id
                """,
                [
                    locked_rule.id,
                    locked_rule.reason,
                    event.id,
                    locked_rule.project_id,
                    *params,
                ],
            )
            rows = cur.fetchall()

        counts = {str(row[0]): row[1] for row in rows}
        event.payload["counts"] = counts
        event.payload["count"] = sum(counts.values())
        event.save(update_fields=["payload"])
        ResultSet.objects.filter(id__in=[row[0] for row in rows]).update(
            modified=timezone.now()
        )
    return event


def revert_rule(rule: IgnoreRule, *, actor=None) -> AuditEvent | None:
    """Revert only rows still ignored by this rule."""
    with transaction.atomic():
        locked_rule = IgnoreRule.objects.select_for_update().filter(pk=rule.pk).first()
        if locked_rule is None:
            return None
        event = AuditEvent.objects.create(
            project=locked_rule.project,
            actor=actor,
            verb=RULE_REVERTED,
            payload={
                "predicate": locked_rule.predicate,
                "counts": {},
                "count": 0,
            },
        )
        with connection.cursor() as cur:
            cur.execute(
                "SELECT set_config('statement_timeout', %s, true)",
                [str(settings.RESEARCH_STATEMENT_TIMEOUT_MS)],
            )
            cur.execute(
                """
                WITH updated AS (
                    UPDATE research_setitem
                    SET disposition = 'u',
                        disposition_by_rule_id = NULL,
                        ignore_reason = '',
                        last_event_id = %s
                    WHERE disposition_by_rule_id = %s
                      AND disposition = 'i'
                    RETURNING result_set_id
                )
                SELECT result_set_id, count(*)
                FROM updated
                GROUP BY result_set_id
                """,
                [event.id, locked_rule.id],
            )
            rows = cur.fetchall()

        counts = {str(row[0]): row[1] for row in rows}
        event.payload["counts"] = counts
        event.payload["count"] = sum(counts.values())
        event.save(update_fields=["payload"])
        ResultSet.objects.filter(id__in=[row[0] for row in rows]).update(
            modified=timezone.now()
        )
    return event


def apply_rule_to_set(
    rule: IgnoreRule,
    result_set: ResultSet,
    *,
    event: AuditEvent,
    only_new: bool = False,
) -> int:
    """Apply one rule to a single set (used by materialization/new matches)."""
    try:
        rule = IgnoreRule.objects.select_for_update().get(pk=rule.pk)
    except IgnoreRule.DoesNotExist:
        return 0
    if not rule.is_active:
        return 0
    spec = _rule_spec(rule)
    if is_empty(spec):
        return 0
    where, params = _counts_sql(spec)
    extra = "AND i.last_event_id = %s" if only_new else ""
    extra_params = [event.id] if only_new else []
    with connection.cursor() as cur:
        cur.execute(
            "SELECT set_config('statement_timeout', %s, true)",
            [str(settings.RESEARCH_STATEMENT_TIMEOUT_MS)],
        )
        cur.execute(
            f"""
            UPDATE research_setitem i
            SET disposition = 'i',
                disposition_by_rule_id = %s,
                ignore_reason = %s,
                last_event_id = %s
            FROM meetings_meetingpage mp
            WHERE i.page_id = mp.id
              AND i.result_set_id = %s
              AND i.disposition = 'u'
              {extra}
              AND {where}
            """,
            [
                rule.id,
                rule.reason,
                event.id,
                result_set.id,
                *extra_params,
                *params,
            ],
        )
        return cur.rowcount


def apply_active_rules_for_set(
    result_set: ResultSet, *, event: AuditEvent | None = None, only_new: bool = False
) -> dict[str, int]:
    """Apply every active project rule to a set; return per-rule counts."""
    if event is None:
        event = record_event(
            result_set.project, "set.materialized", result_set=result_set
        )
    counts: dict[str, int] = {}
    for rule in result_set.project.ignore_rules.filter(is_active=True):
        if is_empty(_rule_spec(rule)):
            continue
        updated = apply_rule_to_set(rule, result_set, event=event, only_new=only_new)
        if updated:
            counts[str(rule.id)] = updated
    return counts
