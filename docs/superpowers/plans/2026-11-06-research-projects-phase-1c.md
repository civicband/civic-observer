# Research Projects — Phase 1C Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add project-wide ignore rules, freezing a lens into a child set, and an explicit "check for new matches" flow, all recorded in the audit trail.

**Architecture:** `research.rules` owns rule preview/apply/revert and applies active rules during materialization and new-match inserts. `research.freeze` copies a lens from a parent set into a child `ResultSet` (`origin=LENS`). `research.new_matches` re-runs a query set's frozen spec with `modified_after`, inserts only new pages, and applies rules only to those new rows. Rule management and the freeze/new-match triggers are HTMX-backed views in `research/views.py`.

**Tech Stack:** Django 5.2, PostgreSQL 17, ParadeDB pg_search, django-rq, htmx, Alpine, Tailwind, pytest, factory_boy.

**Spec:** `docs/superpowers/specs/2026-11-06-research-projects-design.md`

## Global Constraints

- **Choice values are lowercase strings** (`"u"`, `"r"`, `"i"`, `"query"`, `"lens"`, `"manual"`, `"import"`, `"new_matches"`).
- **One event per decision.** Rule lifecycle writes `rule.created` / `rule.changed` / `rule.applied` / `rule.reverted`; freezing writes `set.derived`; new-match checks write `set.new_matches`.
- **Manual beats rules.** A rule only touches `disposition="u"` rows; a manual disposition clears `disposition_by_rule` (already implemented).
- **Rules are project-wide.** Creating/reactivating a rule applies it to every set in the project. Deactivating/deleting reverts only rows still owned by that rule.
- **Sets are snapshots.** `check for new matches` is the only membership change and is explicit/logged. `materialized_at` is the snapshot cutoff and is not advanced by checks; `last_checked_at` is.
- **Set-local only.** Rule preview/apply, lens freezing and new-match inserts read `research_setitem` and the page table through `build_where`; they never scan the corpus for lens browsing.
- **Owner scoping:** every view is `LoginRequiredMixin` and filters through `project__owner=request.user`; other users' projects/sets/rules 404.
- **Page references:** `db_constraint=False, on_delete=DO_NOTHING`; a missing page must not 500.
- **No silent truncation:** sets over `RESEARCH_SET_MAX_ITEMS` remain `TOO_LARGE`; frozen child sets obey the same ceiling.
- **Phase 1C excludes** bulk code application, codebook/memos, methods summary, CSV export and notebook migration. Do not stub them.

## Review Focus

1. A rule applied to a project must never touch reviewed/ignored rows, rows in another project, or manual decisions (Task 1).
2. Deactivating a rule reverts only rows still owned by it, leaving manual decisions and items owned by other rules untouched (Task 1).
3. Materialization and "new matches" must apply rules to the correct scope: the whole new set vs only newly inserted rows (Tasks 1 and 3).
4. Freezing a lens must copy exactly the parent's lens membership, respect dispositions copy/reset, and write `set.derived` (Task 2).
5. "Check for new matches" must use `modified` (not `created`) at/after `last_checked_at or materialized_at`, insert only unseen pages, and advance `last_checked_at` without changing `materialized_at` (Task 3).
6. Every new view must 404 for another user's project/set/rule and require POST + CSRF for mutations (Task 4).
7. A text-based rule must generate the same `build_where` predicate the preview showed; a malformed predicate/cursor must 4xx, not 500 (Tasks 1 and 4).

---

### Task 1: Ignore-rule engine and materialization integration

**Files:**
- Create: `research/rules.py`
- Modify: `research/materialize.py` (apply active rules, audit counts)
- Test: `tests/research/test_rules.py`
- Modify: `tests/research/test_materialize.py`

**Interfaces:**
- Consumes: `QuerySpec`, `build_where`, `IgnoreRule`, `ResultSet`, `SetItem`, `AuditEvent`, `timezone`.
- Produces:
  - `preview_rule(project, spec: QuerySpec, *, sample_limit=10) -> dict` with keys `counts`, `total`, `samples`.
  - `apply_rule_to_project(rule: IgnoreRule, *, actor=None) -> AuditEvent`
  - `revert_rule(rule: IgnoreRule, *, actor=None) -> AuditEvent`
  - `apply_active_rules_for_set(result_set, *, event=None, only_new=False) -> dict[str, int]`
  - `apply_rule_to_set(rule, result_set, *, event, only_new=False) -> int`

- [ ] **Step 1: Write the failing rule-engine tests**

Create `tests/research/test_rules.py`:

```python
import pytest
from unittest.mock import patch

from django.utils import timezone

from research.models import AuditEvent, IgnoreRule, ResultSet, SetItem
from research.rules import (
    apply_rule_to_project,
    apply_rule_to_set,
    preview_rule,
    revert_rule,
)
from research.audit import record_event
from searches.queryspec import QuerySpec
from tests.factories import (
    MeetingDocumentFactory,
    MeetingPageFactory,
    MuniFactory,
    ProjectFactory,
    ResultSetFactory,
    SetItemFactory,
    UserFactory,
)


def _project_with_sets():
    project = ProjectFactory()
    ca = MuniFactory(state="CA")
    or_ = MuniFactory(state="OR")
    ca_doc = MeetingDocumentFactory(municipality=ca, document_type="agenda")
    or_doc = MeetingDocumentFactory(municipality=or_, document_type="minutes")
    ca_page = MeetingPageFactory(document=ca_doc)
    or_page = MeetingPageFactory(document=or_doc)
    first = ResultSetFactory(project=project, name="First")
    second = ResultSetFactory(project=project, name="Second")
    ca_item = SetItemFactory(result_set=first, page=ca_page, disposition="u")
    or_item = SetItemFactory(result_set=second, page=or_page, disposition="u")
    reviewed = SetItemFactory(
        result_set=first, page=MeetingPageFactory(document=ca_doc)
    )
    reviewed.disposition = "r"
    reviewed.save()
    return project, first, second, ca_item, or_item, reviewed


@pytest.mark.django_db
class TestPreviewRule:
    def test_counts_only_unreviewed_in_project(self):
        project, first, second, ca_item, or_item, reviewed = _project_with_sets()
        other_project = ProjectFactory()
        other_set = ResultSetFactory(project=other_project)
        SetItemFactory(result_set=other_set, page=ca_item.page)

        preview = preview_rule(project, QuerySpec(states=("CA",)))

        assert preview["total"] == 1
        assert preview["counts"] == [
            {"result_set_id": first.id, "name": "First", "count": 1}
        ]
        assert preview["samples"][0]["item_id"] == ca_item.id
        assert "meeting_name" in preview["samples"][0]

    def test_empty_when_no_unreviewed_match(self):
        project = ProjectFactory()
        preview = preview_rule(project, QuerySpec(states=("CA",)))
        assert preview == {"counts": [], "total": 0, "samples": []}


@pytest.mark.django_db
class TestApplyRuleToProject:
    def test_ignores_only_unreviewed_items_in_project(self):
        project, first, second, ca_item, or_item, reviewed = _project_with_sets()
        actor = UserFactory()
        rule = IgnoreRule.objects.create(
            project=project,
            name="California",
            predicate=QuerySpec(states=("CA",)).canonical(),
            reason="Out of state",
        )

        event = apply_rule_to_project(rule, actor=actor)

        ca_item.refresh_from_db()
        or_item.refresh_from_db()
        reviewed.refresh_from_db()
        assert ca_item.disposition == "i"
        assert ca_item.disposition_by_rule == rule
        assert ca_item.ignore_reason == "Out of state"
        assert ca_item.last_event == event
        assert or_item.disposition == "u"
        assert reviewed.disposition == "r"
        assert event.verb == "rule.applied"
        assert event.actor == actor
        assert event.payload["counts"] == {str(first.id): 1}
        assert event.payload["count"] == 1

    def test_bumps_modified_only_for_affected_sets(self):
        project, first, second, _, _, _ = _project_with_sets()
        rule = IgnoreRule.objects.create(
            project=project,
            name="California",
            predicate=QuerySpec(states=("CA",)).canonical(),
        )
        before_first = first.modified
        before_second = second.modified

        apply_rule_to_project(rule)

        first.refresh_from_db()
        second.refresh_from_db()
        assert first.modified > before_first
        assert second.modified == before_second

    def test_inactive_rule_is_a_noop(self):
        project, _, _, ca_item, _, _ = _project_with_sets()
        rule = IgnoreRule.objects.create(
            project=project,
            name="California",
            predicate=QuerySpec(states=("CA",)).canonical(),
            is_active=False,
        )

        event = apply_rule_to_project(rule)

        ca_item.refresh_from_db()
        assert ca_item.disposition == "u"
        assert event is None


@pytest.mark.django_db
class TestRevertRule:
    def test_reverts_only_rows_still_owned_by_the_rule(self):
        project, first, second, ca_item, _, _ = _project_with_sets()
        rule = IgnoreRule.objects.create(
            project=project,
            name="California",
            predicate=QuerySpec(states=("CA",)).canonical(),
        )
        other_rule = IgnoreRule.objects.create(
            project=project,
            name="Other",
            predicate=QuerySpec(states=("CA",)).canonical(),
        )
        apply_rule_to_project(rule)
        ca_item.refresh_from_db()
        ca_item.disposition = "i"
        ca_item.disposition_by_rule = other_rule
        ca_item.save()

        event = revert_rule(rule)

        ca_item.refresh_from_db()
        assert ca_item.disposition == "i"
        assert ca_item.disposition_by_rule == other_rule
        assert event.payload["count"] == 0

    def test_reverts_owned_rows_to_unreviewed(self):
        project, first, _, ca_item, _, _ = _project_with_sets()
        rule = IgnoreRule.objects.create(
            project=project,
            name="California",
            predicate=QuerySpec(states=("CA",)).canonical(),
            reason="Out of state",
        )
        apply_rule_to_project(rule)

        event = revert_rule(rule)

        ca_item.refresh_from_db()
        assert ca_item.disposition == "u"
        assert ca_item.disposition_by_rule is None
        assert ca_item.ignore_reason == ""
        assert ca_item.last_event == event
        assert event.verb == "rule.reverted"
        assert event.payload["count"] == 1


@pytest.mark.django_db
class TestApplyActiveRulesForSet:
    def test_applies_rules_to_a_set_and_returns_counts(self):
        project, first, _, ca_item, _, _ = _project_with_sets()
        rule = IgnoreRule.objects.create(
            project=project,
            name="California",
            predicate=QuerySpec(states=("CA",)).canonical(),
        )
        event = record_event(project, "set.materialized", result_set=first)

        counts = apply_active_rules_for_set(first, event=event)

        ca_item.refresh_from_db()
        assert counts == {str(rule.id): 1}
        assert ca_item.disposition == "i"
        assert ca_item.disposition_by_rule == rule
        assert ca_item.last_event == event

    def test_only_new_restricts_to_new_match_items(self):
        project, first, _, ca_item, _, _ = _project_with_sets()
        older = SetItemFactory(
            result_set=first, page=MeetingPageFactory(document=ca_item.page.document)
        )
        older.added_via = "query"
        older.save()
        ca_item.added_via = "new_matches"
        ca_item.save()
        rule = IgnoreRule.objects.create(
            project=project,
            name="California",
            predicate=QuerySpec(states=("CA",)).canonical(),
        )
        event = record_event(project, "set.new_matches", result_set=first)

        counts = apply_active_rules_for_set(first, event=event, only_new=True)

        ca_item.refresh_from_db()
        older.refresh_from_db()
        assert counts == {str(rule.id): 1}
        assert ca_item.disposition == "i"
        assert older.disposition == "u"
```

Append to `tests/research/test_materialize.py`:

```python
@pytest.mark.django_db
def test_materialization_applies_active_rules(settings):
    from research.models import IgnoreRule
    from tests.factories import IgnoreRuleFactory

    settings.RESEARCH_SET_MAX_ITEMS = 500_000
    settings.RESEARCH_INLINE_SET_MAX_ITEMS = 1000
    muni = MuniFactory(state="CA")
    doc = MeetingDocumentFactory(municipality=muni, document_type="agenda")
    page = MeetingPageFactory(document=doc, text="housing")
    project = ProjectFactory()
    IgnoreRuleFactory(
        project=project,
        name="Agendas",
        predicate=QuerySpec(document_type="agenda").canonical(),
        reason="Duplicate of minutes",
    )

    result_set = create_result_set(project, "Housing", QuerySpec(text="housing"))

    item = result_set.items.get(page=page)
    assert item.disposition == "i"
    assert item.disposition_by_rule is not None
    assert item.ignore_reason == "Duplicate of minutes"
    event = AuditEvent.objects.get(project=project, verb="set.materialized")
    assert event.payload["ignored_by_rule"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --group test pytest tests/research/test_rules.py tests/research/test_materialize.py -q --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'research.rules'`.

- [ ] **Step 3: Implement `research/rules.py`**

Create the module with:

```python
"""Project-wide ignore rules over materialized set items."""

from __future__ import annotations

import json
from typing import Any

from django.db import connection, transaction
from django.db.models import Count
from django.utils import timezone

from searches.queryspec import QuerySpec, build_where

from .audit import record_event
from .models import AuditEvent, IgnoreRule, ResultSet, SetItem

RULE_APPLIED = "rule.applied"
RULE_REVERTED = "rule.reverted"


def _rule_spec(rule: IgnoreRule) -> QuerySpec:
    return QuerySpec.from_canonical(rule.predicate or {})


def _counts_sql(spec: QuerySpec) -> tuple[str, list[Any]]:
    where, params = build_where(spec, alias="mp")
    return where, params


def preview_rule(project, spec: QuerySpec, *, sample_limit: int = 10) -> dict:
    """Dry-run a rule: per-set unreviewed counts and a small sample."""
    where, params = _counts_sql(spec)
    counts: list[dict] = []
    with connection.cursor() as cur:
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

    return {
        "counts": counts,
        "total": sum(row["count"] for row in counts),
        "samples": samples,
    }


def apply_rule_to_project(rule: IgnoreRule, *, actor=None) -> AuditEvent | None:
    """Apply one active rule across every set in the project."""
    if not rule.is_active:
        return None
    spec = _rule_spec(rule)
    where, params = _counts_sql(spec)
    preview = preview_rule(rule.project, spec, sample_limit=0)

    with transaction.atomic():
        event = AuditEvent.objects.create(
            project=rule.project,
            actor=actor,
            verb=RULE_APPLIED,
            payload={
                "predicate": rule.predicate,
                "reason": rule.reason,
                "counts": {},
                "count": 0,
            },
        )
        with connection.cursor() as cur:
            cur.execute(
                f"""
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
                """,
                [rule.id, rule.reason, event.id, rule.project_id, *params],
            )
            count = cur.rowcount

        event.payload["counts"] = {
            str(row["result_set_id"]): row["count"] for row in preview["counts"]
        }
        event.payload["count"] = count
        event.save(update_fields=["payload"])
        ResultSet.objects.filter(
            id__in=[row["result_set_id"] for row in preview["counts"]]
        ).update(modified=timezone.now())
    return event


def revert_rule(rule: IgnoreRule, *, actor=None) -> AuditEvent:
    """Revert only rows still ignored by this rule."""
    with transaction.atomic():
        rows = list(
            SetItem.objects.filter(disposition_by_rule=rule, disposition="i")
            .values("result_set_id")
            .annotate(count=Count("id"))
        )
        event = AuditEvent.objects.create(
            project=rule.project,
            actor=actor,
            verb=RULE_REVERTED,
            payload={
                "predicate": rule.predicate,
                "counts": {str(row["result_set_id"]): row["count"] for row in rows},
                "count": sum(row["count"] for row in rows),
            },
        )
        SetItem.objects.filter(disposition_by_rule=rule, disposition="i").update(
            disposition=SetItem.Disposition.UNREVIEWED,
            disposition_by_rule=None,
            ignore_reason="",
            last_event=event,
        )
        ResultSet.objects.filter(id__in=[row["result_set_id"] for row in rows]).update(
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
    spec = _rule_spec(rule)
    where, params = _counts_sql(spec)
    extra = "AND i.added_via = 'new_matches'" if only_new else ""
    with connection.cursor() as cur:
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
            [rule.id, rule.reason, event.id, result_set.id, *params],
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
        updated = apply_rule_to_set(rule, result_set, event=event, only_new=only_new)
        if updated:
            counts[str(rule.id)] = updated
    return counts
```

Replace the odd `__import__` line with a normal import: `from django.db.models import Count` at the top and use `Count("id")`.

- [ ] **Step 4: Integrate active rules into materialization**

In `research/materialize.py`, inside `materialize_result_set`'s atomic block, create the `set.materialized` event before applying rules and update its payload after:

```python
inserted_at = now
AuditEvent.objects.create(...)  # existing block replaced below
```

Concretely, after the insert (SQL/ORM) and before setting `item_count`/`READY`:

```python
event = AuditEvent.objects.create(
    project=result_set.project,
    result_set=result_set,
    verb="set.materialized",
    payload={
        "query": result_set.query,
        "item_count": 0,
        "duration_ms": int((time.monotonic() - started) * 1000),
        "ignored_by_rule": {},
    },
)
from .rules import apply_active_rules_for_set

ignored = apply_active_rules_for_set(result_set, event=event)
result_set.item_count = result_set.items.count()
result_set.materialized_at = now
result_set.status = ResultSet.Status.READY
result_set.save(...)
event.payload["item_count"] = result_set.item_count
event.payload["ignored_by_rule"] = ignored
event.payload["duration_ms"] = int((time.monotonic() - started) * 1000)
event.save(update_fields=["payload"])
```

Remove the old `AuditEvent.objects.create(...)` block so only one `set.materialized` event is written. Keep the `transaction.atomic()`.

- [ ] **Step 5: Run the rule and materialization tests**

Run: `uv run --group test pytest tests/research/test_rules.py tests/research/test_materialize.py -q --no-cov`
Expected: PASS.

- [ ] **Step 6: Run the research suite**

Run: `uv run --group test pytest tests/research -q --no-cov`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add research/rules.py research/materialize.py tests/research/test_rules.py tests/research/test_materialize.py
git commit -m "feat: add project-wide ignore rule engine"
```

---

### Task 2: Freeze a lens into a child set

**Files:**
- Modify: `research/lens.py` (`canonical`, `from_canonical`)
- Create: `research/freeze.py`
- Modify: `research/tasks.py` (`materialize_lens_set_task`)
- Modify: `research/views.py` (`FreezeLensView`), `research/urls.py`
- Modify: `templates/research/set_detail.html` (freeze form)
- Test: `tests/research/test_lens.py`, `tests/research/test_freeze.py`, `tests/research/test_views.py`

**Interfaces:**
- Consumes: `Lens`, `filter_items`, `ResultSet`, `SetItem`, `AuditEvent`, `apply_active_rules_for_set`.
- Produces:
  - `Lens.canonical() -> dict`, `Lens.from_canonical(data) -> Lens`
  - `freeze_lens(parent: ResultSet, name: str, lens: Lens, *, copy_dispositions=True) -> ResultSet`
  - `materialize_lens_set(result_set_id) -> ResultSet`
  - `enqueue_lens_materialization(result_set_id) -> None`
  - URL name `research:set-freeze` → `POST <project>/sets/<set>/freeze/`

- [ ] **Step 1: Write the failing Lens canonical tests**

Append to `tests/research/test_lens.py`:

```python
def test_canonical_round_trip():
    municipality = uuid.uuid4()
    code = uuid.uuid4()
    lens = Lens(
        text="housing",
        dispositions=("i",),
        codes_all=(code,),
        uncoded=True,
        municipality_ids=(municipality,),
        states=("CA",),
        date_from=date(2024, 1, 1),
        date_to=date(2024, 12, 31),
        document_type="agenda",
        added_via=("new_matches",),
        order="date_desc",
    )

    rebuilt = Lens.from_canonical(lens.canonical())

    assert rebuilt == lens
```

- [ ] **Step 2: Write the failing freeze tests**

Create `tests/research/test_freeze.py`:

```python
import pytest

from research.freeze import freeze_lens, materialize_lens_set
from research.lens import Lens
from research.models import AuditEvent, ResultSet
from tests.factories import (
    MeetingDocumentFactory,
    MeetingPageFactory,
    MuniFactory,
    ProjectFactory,
    ResultSetFactory,
    SetItemFactory,
)


def _parent_with_items():
    project = ProjectFactory()
    parent = ResultSetFactory(project=project, name="Parent")
    ca = MuniFactory(state="CA")
    ca_doc = MeetingDocumentFactory(municipality=ca)
    or_doc = MeetingDocumentFactory(municipality=MuniFactory(state="OR"))
    ca_page = MeetingPageFactory(document=ca_doc, text="housing")
    or_page = MeetingPageFactory(document=or_doc, text="housing")
    ca_item = SetItemFactory(result_set=parent, page=ca_page, disposition="r")
    or_item = SetItemFactory(result_set=parent, page=or_page, disposition="u")
    ignored = SetItemFactory(
        result_set=parent, page=MeetingPageFactory(document=ca_doc), disposition="i"
    )
    return project, parent, ca_item, or_item, ignored


@pytest.mark.django_db
class TestFreezeLens:
    def test_copies_only_lens_items_and_copies_dispositions(self, settings):
        settings.RESEARCH_SET_MAX_ITEMS = 500_000
        settings.RESEARCH_INLINE_SET_MAX_ITEMS = 1000
        project, parent, ca_item, or_item, ignored = _parent_with_items()

        child = freeze_lens(
            parent, "CA only", Lens(states=("CA",), dispositions=("u", "r", "i"))
        )

        assert child.origin == ResultSet.Origin.LENS
        assert child.parent == parent
        assert child.status == ResultSet.Status.READY
        assert set(child.items.values_list("page_id", flat=True)) == {
            ca_item.page_id,
            ignored.page_id,
        }
        copied = child.items.get(page_id=ca_item.page_id)
        assert copied.disposition == "r"
        assert copied.added_via == ResultSet.Origin.LENS

    def test_reset_dispositions_option(self, settings):
        settings.RESEARCH_SET_MAX_ITEMS = 500_000
        settings.RESEARCH_INLINE_SET_MAX_ITEMS = 1000
        project, parent, ca_item, _, _ = _parent_with_items()

        child = freeze_lens(
            parent, "CA only", Lens(states=("CA",)), copy_dispositions=False
        )

        assert child.items.get(page_id=ca_item.page_id).disposition == "u"

    def test_writes_set_derived_audit(self, settings):
        settings.RESEARCH_SET_MAX_ITEMS = 500_000
        settings.RESEARCH_INLINE_SET_MAX_ITEMS = 1000
        project, parent, _, _, _ = _parent_with_items()

        child = freeze_lens(parent, "CA only", Lens(states=("CA",)))

        event = AuditEvent.objects.get(project=project, verb="set.derived")
        assert event.result_set == child
        assert event.payload["parent"] == str(parent.id)
        assert event.payload["item_count"] == child.item_count
        assert event.payload["dispositions_copied"] is True

    def test_over_ceiling_is_too_large(self, settings):
        settings.RESEARCH_SET_MAX_ITEMS = 1
        project, parent, _, _, _ = _parent_with_items()

        child = freeze_lens(parent, "Too big", Lens())

        assert child.status == ResultSet.Status.TOO_LARGE
        assert child.items.count() == 0

    def test_rerun_is_idempotent(self, settings):
        settings.RESEARCH_SET_MAX_ITEMS = 500_000
        settings.RESEARCH_INLINE_SET_MAX_ITEMS = 1000
        project, parent, ca_item, _, _ = _parent_with_items()
        child = freeze_lens(parent, "CA only", Lens(states=("CA",)))

        materialize_lens_set(child.id)

        assert child.items.count() == 2
```

- [ ] **Step 3: Write the failing view test**

Append to `tests/research/test_views.py`:

```python
@pytest.mark.django_db
class TestFreezeLensView:
    def _url(self, result_set):
        return reverse(
            "research:set-freeze", args=[result_set.project_id, result_set.id]
        )

    def test_requires_login(self, client):
        result_set = ResultSetFactory()
        assert client.post(self._url(result_set)).status_code == 302

    def test_cannot_freeze_another_users_set(self, client):
        result_set = ResultSetFactory()
        client.force_login(UserFactory())
        assert client.post(self._url(result_set)).status_code == 404

    def test_creates_child_set(self, client, settings):
        settings.RESEARCH_SET_MAX_ITEMS = 500_000
        settings.RESEARCH_INLINE_SET_MAX_ITEMS = 1000
        user = UserFactory()
        project = ProjectFactory(owner=user)
        parent = ResultSetFactory(project=project)
        item = SetItemFactory(result_set=parent, disposition="u")
        client.force_login(user)

        response = client.post(
            self._url(parent),
            {"name": "Child", "lens": "", "copy_dispositions": "1"},
        )

        assert response.status_code == 302
        child = ResultSet.objects.get(project=project, name="Child")
        assert child.parent == parent
        assert child.items.get(page_id=item.page_id)
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `uv run --group test pytest tests/research/test_lens.py tests/research/test_freeze.py tests/research/test_views.py -q --no-cov`
Expected: FAIL with missing `canonical`/`from_canonical`/`research.freeze`/`research:set-freeze`.

- [ ] **Step 5: Add `Lens.canonical`/`from_canonical`**

In `research/lens.py`, add:

```python
def canonical(self) -> dict:
    return {
        "text": self.text,
        "dispositions": list(self.dispositions),
        "codes_all": [str(value) for value in self.codes_all],
        "codes_any": [str(value) for value in self.codes_any],
        "codes_none": [str(value) for value in self.codes_none],
        "uncoded": self.uncoded,
        "municipality_ids": [str(value) for value in self.municipality_ids],
        "states": list(self.states),
        "date_from": self.date_from.isoformat() if self.date_from else None,
        "date_to": self.date_to.isoformat() if self.date_to else None,
        "document_type": self.document_type,
        "added_via": list(self.added_via),
        "order": self.order,
    }


@classmethod
def from_canonical(cls, data: dict) -> "Lens":
    return cls(
        text=data.get("text", ""),
        dispositions=tuple(data.get("dispositions", DEFAULT_DISPOSITIONS)),
        codes_all=tuple(uuid.UUID(value) for value in data.get("codes_all", [])),
        codes_any=tuple(uuid.UUID(value) for value in data.get("codes_any", [])),
        codes_none=tuple(uuid.UUID(value) for value in data.get("codes_none", [])),
        uncoded=bool(data.get("uncoded", False)),
        municipality_ids=tuple(
            uuid.UUID(value) for value in data.get("municipality_ids", [])
        ),
        states=tuple(data.get("states", [])),
        date_from=(
            date.fromisoformat(data["date_from"]) if data.get("date_from") else None
        ),
        date_to=date.fromisoformat(data["date_to"]) if data.get("date_to") else None,
        document_type=data.get("document_type", "all"),
        added_via=tuple(data.get("added_via", [])),
        order=data.get("order", "rank"),
    )
```

- [ ] **Step 6: Implement `research/freeze.py`**

```python
"""Freeze a lens over a parent set into a child result set."""

from __future__ import annotations

import hashlib
import json

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .audit import record_event
from .browse import filter_items
from .lens import Lens
from .models import AuditEvent, ResultSet, SetItem
from .rules import apply_active_rules_for_set

BATCH = 1000


def _child_query(parent: ResultSet, lens: Lens, copy_dispositions: bool) -> dict:
    return {
        "parent": str(parent.id),
        "lens": lens.canonical(),
        "copy_dispositions": copy_dispositions,
    }


def _query_hash(query: dict) -> str:
    return hashlib.sha256(
        json.dumps(query, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def freeze_lens(
    parent: ResultSet,
    name: str,
    lens: Lens,
    *,
    copy_dispositions: bool = True,
) -> ResultSet:
    query = _child_query(parent, lens, copy_dispositions)
    child = ResultSet.objects.create(
        project=parent.project,
        parent=parent,
        name=name,
        origin=ResultSet.Origin.LENS,
        query=query,
        query_hash=_query_hash(query),
        status=ResultSet.Status.PENDING,
    )
    count = filter_items(parent, lens).count()
    if count > settings.RESEARCH_SET_MAX_ITEMS:
        child.status = ResultSet.Status.TOO_LARGE
        child.item_count = count
        child.error = (
            f"This lens matches at least {count} pages, more than the "
            f"{settings.RESEARCH_SET_MAX_ITEMS:,}-page set limit."
        )
        child.save(update_fields=["status", "item_count", "error", "modified"])
        return child
    if count <= settings.RESEARCH_INLINE_SET_MAX_ITEMS:
        return materialize_lens_set(child.id)
    enqueue_lens_materialization(child.id)
    return child


def enqueue_lens_materialization(result_set_id) -> None:
    import django_rq

    from .tasks import materialize_lens_set_task

    queue = django_rq.get_queue(settings.RESEARCH_QUEUE)
    queue.enqueue(
        materialize_lens_set_task,
        result_set_id,
        job_timeout=settings.RESEARCH_JOB_TIMEOUT,
    )


def materialize_lens_set(result_set_id) -> ResultSet:
    child = ResultSet.objects.get(pk=result_set_id)
    lens = Lens.from_canonical((child.query or {}).get("lens", {}))
    parent_id = (child.query or {}).get("parent")
    parent = ResultSet.objects.get(pk=parent_id)
    copy_dispositions = bool((child.query or {}).get("copy_dispositions", True))
    now = timezone.now()
    child.status = ResultSet.Status.MATERIALIZING
    child.error = ""
    child.save(update_fields=["status", "error", "modified"])
    try:
        with transaction.atomic():
            source = filter_items(parent, lens).values(
                "page_id",
                "rank",
                "meeting_date",
                "municipality_id",
                "state",
                "document_type",
                "disposition",
                "ignore_reason",
            )
            batch: list[SetItem] = []
            for row in source.iterator(chunk_size=BATCH):
                batch.append(
                    SetItem(
                        result_set=child,
                        page_id=row["page_id"],
                        rank=row["rank"],
                        meeting_date=row["meeting_date"],
                        municipality_id=row["municipality_id"],
                        state=row["state"],
                        document_type=row["document_type"],
                        disposition=(
                            row["disposition"]
                            if copy_dispositions
                            else SetItem.Disposition.UNREVIEWED
                        ),
                        previous_disposition="",
                        disposition_by_rule=None,
                        ignore_reason=row["ignore_reason"] if copy_dispositions else "",
                        added_via=SetItem.AddedVia.LENS,
                        added_at=now,
                    )
                )
                if len(batch) >= BATCH:
                    SetItem.objects.bulk_create(
                        batch, ignore_conflicts=True, batch_size=BATCH
                    )
                    batch = []
            if batch:
                SetItem.objects.bulk_create(
                    batch, ignore_conflicts=True, batch_size=BATCH
                )

            event = record_event(
                child.project,
                "set.derived",
                result_set=child,
                payload={
                    "parent": str(parent.id),
                    "lens": lens.canonical(),
                    "item_count": 0,
                    "dispositions_copied": copy_dispositions,
                    "ignored_by_rule": {},
                },
            )
            ignored = apply_active_rules_for_set(child, event=event)
            child.item_count = child.items.count()
            child.materialized_at = now
            child.status = ResultSet.Status.READY
            child.save(
                update_fields=["item_count", "materialized_at", "status", "modified"]
            )
            event.payload["item_count"] = child.item_count
            event.payload["ignored_by_rule"] = ignored
            event.save(update_fields=["payload"])
    except Exception as exc:
        child.status = ResultSet.Status.FAILED
        child.error = str(exc)
        child.save(update_fields=["status", "error", "modified"])
        raise
    return child
```

Important: `freeze_lens` must store `copy_dispositions` in `query` so `materialize_lens_set` can read it. Add `query["copy_dispositions"] = copy_dispositions` before creating the child. Update `_child_query` to accept it. The tests call the service directly with `copy_dispositions=False`; the child query must carry it.

- [ ] **Step 7: Add the task and view/URL**

`research/tasks.py`:

```python
def materialize_lens_set_task(result_set_id) -> None:
    from .freeze import materialize_lens_set

    materialize_lens_set(result_set_id)
```

`research/views.py`:

```python
class FreezeLensView(LoginRequiredMixin, View):
    def post(self, request, project, set):
        parent = get_object_or_404(
            ResultSet, id=set, project_id=project, project__owner=request.user
        )
        lens = Lens.from_querydict(QueryDict(request.POST.get("lens", "")))
        name = (request.POST.get("name") or "").strip()
        if not name:
            return HttpResponseBadRequest("A name is required")
        copy_dispositions = request.POST.get("copy_dispositions") == "1"
        from .freeze import freeze_lens

        child = freeze_lens(parent, name, lens, copy_dispositions=copy_dispositions)
        return redirect("research:set-detail", project=parent.project_id, set=child.id)
```

`research/urls.py`:

```python
path(
    "<uuid:project>/sets/<uuid:set>/freeze/",
    views.FreezeLensView.as_view(),
    name="set-freeze",
),
```

- [ ] **Step 8: Add the freeze form to the set detail template**

Inside the header row, next to Review, add a form:

```html
<form method="post" action="{% url 'research:set-freeze' result_set.project_id result_set.id %}" class="mt-4 sm:mt-0 flex items-center gap-2">
    {% csrf_token %}
    <input type="hidden" name="lens" value="{{ querystring }}">
    <input type="text" name="name" required placeholder="Child set name"
           class="rounded-md border-gray-300 text-sm shadow-sm">
    <label class="flex items-center gap-1 text-xs text-gray-600">
        <input type="checkbox" name="copy_dispositions" value="1" checked>
        Copy dispositions
    </label>
    <button type="submit" class="inline-flex items-center px-3 py-1.5 border border-gray-300 text-xs font-medium rounded-md text-gray-700 bg-white hover:bg-gray-50">Save lens as set</button>
</form>
```

- [ ] **Step 9: Run the tests**

Run: `uv run --group test pytest tests/research/test_lens.py tests/research/test_freeze.py tests/research/test_views.py -q --no-cov`
Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add research/lens.py research/freeze.py research/tasks.py research/views.py research/urls.py templates/research/set_detail.html tests/research/test_lens.py tests/research/test_freeze.py tests/research/test_views.py
git commit -m "feat: freeze lenses into child research sets"
```

---

### Task 3: Check for new matches

**Files:**
- Create: `research/new_matches.py`
- Modify: `research/tasks.py` (`check_new_matches_task`)
- Modify: `research/views.py` (`NewMatchesView`), `research/urls.py`
- Modify: `templates/research/set_detail.html` (button + last-checked text)
- Test: `tests/research/test_new_matches.py`, `tests/research/test_views.py`

**Interfaces:**
- Consumes: `QuerySpec`, `build_where`, `ResultSet`, `SetItem`, `AuditEvent`, `apply_active_rules_for_set`, `search_backends`, settings.
- Produces:
  - `check_for_new_matches(result_set, *, actor=None) -> AuditEvent`
  - `create_new_matches(result_set, *, actor=None) -> ResultSet` (inline or enqueue)
  - `enqueue_new_matches(result_set_id) -> None`
  - URL name `research:set-new-matches` → `POST <project>/sets/<set>/new-matches/`

- [ ] **Step 1: Write the failing tests**

Create `tests/research/test_new_matches.py`:

```python
import pytest
from unittest.mock import patch

from django.utils import timezone

from research.models import AuditEvent, IgnoreRule, ResultSet
from research.new_matches import check_for_new_matches, create_new_matches
from searches.queryspec import QuerySpec
from tests.factories import (
    IgnoreRuleFactory,
    MeetingDocumentFactory,
    MeetingPageFactory,
    MuniFactory,
    ProjectFactory,
    ResultSetFactory,
    SetItemFactory,
    UserFactory,
)


@pytest.mark.django_db
class TestCheckForNewMatches:
    def test_inserts_only_pages_modified_after_cutoff(self, settings):
        settings.RESEARCH_USE_SQL_MATERIALIZATION = False
        project = ProjectFactory()
        result_set = ResultSetFactory(
            project=project,
            query=QuerySpec(text="housing").canonical(),
            status=ResultSet.Status.READY,
        )
        cutoff = timezone.now()
        result_set.last_checked_at = cutoff
        result_set.materialized_at = cutoff
        result_set.save()
        muni = MuniFactory()
        doc = MeetingDocumentFactory(municipality=muni)
        old = MeetingPageFactory(document=doc, text="housing")
        new = MeetingPageFactory(document=doc, text="housing")
        SetItemFactory(result_set=result_set, page=old)
        from meetings.models import MeetingPage

        MeetingPage.objects.filter(pk=old.pk).update(
            modified=cutoff - timezone.timedelta(days=1)
        )
        MeetingPage.objects.filter(pk=new.pk).update(
            modified=cutoff + timezone.timedelta(days=1)
        )

        event = check_for_new_matches(result_set)

        result_set.refresh_from_db()
        assert result_set.items.filter(page=new).exists()
        assert result_set.items.filter(page=old).count() == 1
        assert result_set.items.get(page=new).added_via == "new_matches"
        assert result_set.items.get(page=new).disposition == "u"
        assert result_set.last_checked_at > cutoff
        assert result_set.materialized_at == cutoff
        assert event.verb == "set.new_matches"
        assert event.payload["added"] == 1

    def test_only_query_origin_allowed(self):
        result_set = ResultSetFactory(origin=ResultSet.Origin.LENS)
        with pytest.raises(ValueError):
            check_for_new_matches(result_set)

    def test_applies_rules_to_new_items_only(self, settings):
        settings.RESEARCH_USE_SQL_MATERIALIZATION = False
        project = ProjectFactory()
        result_set = ResultSetFactory(
            project=project,
            query=QuerySpec(text="housing").canonical(),
            status=ResultSet.Status.READY,
        )
        cutoff = timezone.now() - timezone.timedelta(days=1)
        result_set.last_checked_at = cutoff
        result_set.save()
        old = SetItemFactory(result_set=result_set)
        old_page = old.page
        # The old item is already in the set; make it match the search too.
        from meetings.models import MeetingPage

        MeetingPage.objects.filter(pk=old_page.pk).update(
            text="housing", modified=cutoff - timezone.timedelta(hours=1)
        )
        new_page = MeetingPageFactory(
            text="housing", document=old_page.document, page_number=99
        )
        MeetingPage.objects.filter(pk=new_page.pk).update(
            modified=cutoff + timezone.timedelta(hours=1)
        )
        IgnoreRuleFactory(
            project=project,
            predicate=QuerySpec(document_type="agenda").canonical(),
            reason="Agendas",
        )

        event = check_for_new_matches(result_set)

        old.refresh_from_db()
        new_item = result_set.items.get(page=new_page)
        assert old.disposition == "u"
        assert new_item.disposition == "i"
        assert new_item.disposition_by_rule is not None
        assert event.payload["ignored_by_rule"]

    def test_enqueue_when_over_inline(self, settings):
        settings.RESEARCH_INLINE_SET_MAX_ITEMS = 1
        result_set = ResultSetFactory(
            project=ProjectFactory(),
            query=QuerySpec(text="housing").canonical(),
            status=ResultSet.Status.READY,
        )
        with patch(
            "research.new_matches.count_new_matches", return_value=5
        ) as mock_count:
            create_new_matches(result_set)
        mock_count.assert_called_once_with(result_set)
```

(Use a normal `unittest.mock.patch` instead of the `MonkeyPatch` block if the repo's style prefers it.)

Add a view test to `tests/research/test_views.py`:

```python
@pytest.mark.django_db
class TestNewMatchesView:
    def _url(self, result_set):
        return reverse(
            "research:set-new-matches", args=[result_set.project_id, result_set.id]
        )

    def test_requires_login(self, client):
        result_set = ResultSetFactory()
        assert client.post(self._url(result_set)).status_code == 302

    def test_cannot_check_another_users_set(self, client):
        result_set = ResultSetFactory()
        client.force_login(UserFactory())
        assert client.post(self._url(result_set)).status_code == 404

    def test_non_query_origin_returns_409(self, client):
        user = UserFactory()
        project = ProjectFactory(owner=user)
        result_set = ResultSetFactory(project=project, origin=ResultSet.Origin.LENS)
        client.force_login(user)
        assert client.post(self._url(result_set)).status_code == 409

    def test_query_origin_runs_and_refreshes(self, client, settings):
        settings.RESEARCH_USE_SQL_MATERIALIZATION = False
        user = UserFactory()
        project = ProjectFactory(owner=user)
        result_set = ResultSetFactory(
            project=project,
            query=QuerySpec(text="housing").canonical(),
            status=ResultSet.Status.READY,
        )
        client.force_login(user)
        response = client.post(self._url(result_set))
        assert response.status_code == 200
        assert response["HX-Refresh"] == "true"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --group test pytest tests/research/test_new_matches.py -q --no-cov`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `research/new_matches.py`**

```python
"""Explicitly re-run a set's frozen query for pages changed since the last check."""

from __future__ import annotations

import time

from django.conf import settings
from django.db import connection, transaction
from django.db.models import Count
from django.utils import timezone

from meetings.models import MeetingPage
from searches import search_backends
from searches.queryspec import QuerySpec, build_where

from .audit import record_event
from .models import AuditEvent, ResultSet, SetItem
from .rules import apply_active_rules_for_set

PAGE_SIZE = 1000


def _cutoff(result_set: ResultSet):
    return (
        result_set.last_checked_at or result_set.materialized_at or result_set.created
    )


def _spec(result_set: ResultSet, cutoff) -> QuerySpec:
    return QuerySpec.from_canonical(result_set.query or {}).with_modified_after(cutoff)


def count_new_matches(result_set: ResultSet) -> int:
    cutoff = _cutoff(result_set)
    spec = _spec(result_set, cutoff)
    return search_backends.get_search_backend().count_matches(
        spec, settings.RESEARCH_INLINE_SET_MAX_ITEMS + 1
    )


def check_for_new_matches(result_set: ResultSet, *, actor=None) -> AuditEvent:
    if result_set.origin != ResultSet.Origin.QUERY:
        raise ValueError("Only query-origin sets can check for new matches")
    cutoff = _cutoff(result_set)
    spec = _spec(result_set, cutoff)
    started = time.monotonic()
    with transaction.atomic():
        event = AuditEvent.objects.create(
            project=result_set.project,
            actor=actor,
            result_set=result_set,
            verb="set.new_matches",
            payload={"cutoff": cutoff.isoformat(), "added": 0, "ignored_by_rule": {}},
        )
        if settings.RESEARCH_USE_SQL_MATERIALIZATION:
            added = _insert_sql(result_set, spec, event)
        else:
            added = _insert_orm(result_set, spec)
        ignored = apply_active_rules_for_set(result_set, event=event, only_new=True)
        result_set.item_count = result_set.items.count()
        result_set.last_checked_at = timezone.now()
        result_set.save(update_fields=["item_count", "last_checked_at", "modified"])
        event.payload["added"] = added
        event.payload["ignored_by_rule"] = ignored
        event.payload["duration_ms"] = int((time.monotonic() - started) * 1000)
        event.save(update_fields=["payload"])
    return event


def create_new_matches(result_set: ResultSet, *, actor=None) -> ResultSet:
    if result_set.origin != ResultSet.Origin.QUERY:
        raise ValueError("Only query-origin sets can check for new matches")
    if count_new_matches(result_set) > settings.RESEARCH_INLINE_SET_MAX_ITEMS:
        enqueue_new_matches(result_set.id)
        return result_set
    check_for_new_matches(result_set, actor=actor)
    return result_set


def enqueue_new_matches(result_set_id) -> None:
    import django_rq

    from .tasks import check_new_matches_task

    queue = django_rq.get_queue(settings.RESEARCH_QUEUE)
    queue.enqueue(
        check_new_matches_task,
        result_set_id,
        job_timeout=settings.RESEARCH_JOB_TIMEOUT,
    )


def _insert_sql(result_set: ResultSet, spec: QuerySpec, event: AuditEvent) -> int:
    where_sql, params = build_where(spec)
    rank_sql = "paradedb.score(mp.id)" if (spec.text or spec.meeting_name) else "NULL"
    with connection.cursor() as cur:
        cur.execute(
            "SELECT set_config('statement_timeout', %s, true)",
            [str(settings.RESEARCH_STATEMENT_TIMEOUT_MS)],
        )
        cur.execute(
            f"""
            INSERT INTO research_setitem
                (result_set_id, page_id, rank, meeting_date, municipality_id,
                 state, document_type, disposition, previous_disposition,
                 ignore_reason, last_event_id, added_via, added_at)
            SELECT %s, mp.id, {rank_sql}, mp.meeting_date, mp.municipality_id,
                   mp.state, mp.document_type, 'u', '', '', %s, %s, now()
            FROM meetings_meetingpage mp
            WHERE {where_sql}
            ON CONFLICT (result_set_id, page_id) DO NOTHING
            """,
            [result_set.id, event.id, SetItem.AddedVia.NEW_MATCHES, *params],
        )
        return cur.rowcount


def _insert_orm(result_set: ResultSet, spec: QuerySpec) -> int:
    backend = search_backends.get_search_backend()
    before = result_set.items.count()
    offset = 0
    while True:
        results, _ = backend.search(
            query_text=spec.text,
            municipalities=list(spec.municipality_ids),
            states=list(spec.states),
            date_from=spec.date_from,
            date_to=spec.date_to,
            document_type=spec.document_type,
            meeting_name_query=spec.meeting_name,
            modified_after=spec.modified_after,
            limit=PAGE_SIZE,
            offset=offset,
        )
        if not results:
            break
        ids = [row["id"] for row in results]
        pages = MeetingPage.objects.in_bulk(ids)
        SetItem.objects.bulk_create(
            [
                SetItem(
                    result_set=result_set,
                    page=pages[page_id],
                    meeting_date=pages[page_id].meeting_date,
                    municipality_id=pages[page_id].municipality_id,
                    state=pages[page_id].state,
                    document_type=pages[page_id].document_type,
                    added_via=SetItem.AddedVia.NEW_MATCHES,
                )
                for page_id in ids
                if page_id in pages
            ],
            ignore_conflicts=True,
        )
        if len(results) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return result_set.items.count() - before
```

`research/tasks.py`:

```python
def check_new_matches_task(result_set_id) -> None:
    from .new_matches import check_for_new_matches
    from .models import ResultSet

    check_for_new_matches(ResultSet.objects.get(pk=result_set_id))
```

- [ ] **Step 4: Add the view/URL and template button**

`research/views.py`:

```python
class NewMatchesView(LoginRequiredMixin, View):
    def post(self, request, project, set):
        result_set = get_object_or_404(
            ResultSet, id=set, project_id=project, project__owner=request.user
        )
        from .new_matches import create_new_matches

        try:
            create_new_matches(result_set, actor=request.user)
        except ValueError:
            return HttpResponse("Only query sets can check for new matches", status=409)
        response = HttpResponse("")
        response["HX-Refresh"] = "true"
        return response
```

`research/urls.py`:

```python
path(
    "<uuid:project>/sets/<uuid:set>/new-matches/",
    views.NewMatchesView.as_view(),
    name="set-new-matches",
),
```

In `templates/research/set_detail.html`, next to Review, add for `origin == "query"` and `status == "ready"`:

```html
<form method="post" action="{% url 'research:set-new-matches' result_set.project_id result_set.id %}"
      class="mt-4 sm:mt-0 ml-2">
    {% csrf_token %}
    <button type="submit" class="inline-flex items-center px-4 py-2 border border-gray-300 text-sm font-medium rounded-md shadow-sm text-gray-700 bg-white hover:bg-gray-50">
        Check for new matches
    </button>
</form>
```

And show `last_checked_at` under the title when present.

- [ ] **Step 5: Run the tests**

Run: `uv run --group test pytest tests/research/test_new_matches.py tests/research/test_views.py -q --no-cov`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add research/new_matches.py research/tasks.py research/views.py research/urls.py templates/research/set_detail.html tests/research/test_new_matches.py tests/research/test_views.py
git commit -m "feat: add explicit check-for-new-matches flow"
```

---

### Task 4: Rule management UI and entry points

**Files:**
- Modify: `research/views.py` (`IgnoreRuleListView`, `IgnoreRuleCreateView`, `IgnoreRuleToggleView`, `IgnoreRuleDeleteView`, `ignore_reasons` context)
- Modify: `research/urls.py`
- Create: `templates/research/rules/list.html`, `templates/research/rules/form.html`
- Modify: `templates/research/partials/item.html` (Ignore pages like this link, reason datalist)
- Modify: `templates/research/partials/review_item.html` (reason datalist)
- Modify: `templates/research/set_detail.html` (Make ignore rule link)
- Modify: `templates/research/project_detail.html` (Rules link)
- Test: `tests/research/test_rule_views.py`

**Interfaces:**
- Consumes: `preview_rule`, `apply_rule_to_project`, `revert_rule`, `IgnoreRule`, `QuerySpec`, `Lens`, `enqueue` on the research queue.
- Produces URL names:
  - `research:rule-list` → `GET <project>/rules/`
  - `research:rule-create` → `GET/POST <project>/rules/new/`
  - `research:rule-toggle` → `POST <project>/rules/<uuid:rule>/toggle/`
  - `research:rule-delete` → `POST <project>/rules/<uuid:rule>/delete/`
- Adds `ignore_reasons` to the set detail/review/disposition/undo contexts.

- [ ] **Step 1: Write the failing view tests**

Create `tests/research/test_rule_views.py`:

```python
from unittest.mock import patch

import pytest
from django.test import override_settings
from django.urls import reverse

from research.models import AuditEvent, IgnoreRule, ResultSet
from tests.factories import (
    IgnoreRuleFactory,
    ProjectFactory,
    ResultSetFactory,
    SetItemFactory,
    UserFactory,
)


@pytest.mark.django_db
class TestRuleViews:
    def _project(self):
        user = UserFactory()
        return user, ProjectFactory(owner=user)

    def test_rule_list_requires_login(self, client):
        _, project = self._project()
        assert (
            client.get(reverse("research:rule-list", args=[project.id])).status_code
            == 302
        )

    def test_rule_list_owner_scoped(self, client):
        user, project = self._project()
        mine = IgnoreRuleFactory(project=project, name="Mine")
        other = IgnoreRuleFactory(project=ProjectFactory(), name="Other")
        client.force_login(user)

        response = client.get(reverse("research:rule-list", args=[project.id]))

        content = response.content.decode()
        assert response.status_code == 200
        assert "Mine" in content
        assert "Other" not in content

    def test_create_requires_login(self, client):
        _, project = self._project()
        assert (
            client.get(reverse("research:rule-create", args=[project.id])).status_code
            == 302
        )

    def test_create_prefills_from_item(self, client):
        user, project = self._project()
        item = SetItemFactory(result_set=ResultSetFactory(project=project))
        client.force_login(user)

        response = client.get(
            reverse("research:rule-create", args=[project.id]) + f"?page={item.id}"
        )

        content = response.content.decode()
        assert response.status_code == 200
        assert item.page.meeting_name in content
        assert "agenda" in content.lower()

    def test_create_previews_without_saving(self, client):
        user, project = self._project()
        client.force_login(user)

        response = client.post(
            reverse("research:rule-create", args=[project.id]),
            {"name": "CA", "states": "CA", "action": "preview"},
        )

        assert response.status_code == 200
        assert "Preview" in response.content.decode()
        assert not IgnoreRule.objects.filter(project=project).exists()

    def test_create_saves_and_enqueues(self, client, settings):
        user, project = self._project()
        client.force_login(user)

        with patch("django_rq.get_queue") as get_queue:
            response = client.post(
                reverse("research:rule-create", args=[project.id]),
                {
                    "name": "CA",
                    "states": "CA",
                    "reason": "Out of state",
                    "action": "save",
                },
            )

        assert response.status_code == 302
        rule = IgnoreRule.objects.get(project=project)
        assert rule.predicate["states"] == ["CA"]
        assert rule.reason == "Out of state"
        assert AuditEvent.objects.filter(project=project, verb="rule.created").exists()
        get_queue.assert_called_once_with(settings.RESEARCH_QUEUE)

    def test_toggle_off_enqueues_revert(self, client, settings):
        user, project = self._project()
        rule = IgnoreRuleFactory(project=project, is_active=True)
        client.force_login(user)

        with patch("django_rq.get_queue") as get_queue:
            response = client.post(
                reverse("research:rule-toggle", args=[project.id, rule.id])
            )

        assert response.status_code == 302
        rule.refresh_from_db()
        assert rule.is_active is False
        assert AuditEvent.objects.filter(project=project, verb="rule.changed").exists()
        get_queue.assert_called_once_with(settings.RESEARCH_QUEUE)

    def test_toggle_on_enqueues_apply(self, client, settings):
        user, project = self._project()
        rule = IgnoreRuleFactory(project=project, is_active=False)
        client.force_login(user)

        with patch("django_rq.get_queue") as get_queue:
            client.post(reverse("research:rule-toggle", args=[project.id, rule.id]))

        rule.refresh_from_db()
        assert rule.is_active is True
        get_queue.assert_called_once_with(settings.RESEARCH_QUEUE)

    def test_delete_reverts_and_deletes(self, client):
        user, project = self._project()
        item = SetItemFactory(
            result_set=ResultSetFactory(project=project), disposition="i"
        )
        rule = IgnoreRuleFactory(project=project)
        item.disposition_by_rule = rule
        item.save()
        client.force_login(user)

        response = client.post(
            reverse("research:rule-delete", args=[project.id, rule.id])
        )

        assert response.status_code == 302
        assert not IgnoreRule.objects.filter(id=rule.id).exists()
        item.refresh_from_db()
        assert item.disposition == "u"
        assert item.disposition_by_rule is None

    def test_item_card_has_ignore_like_this_and_datalist(self, client):
        user, project = self._project()
        result_set = ResultSetFactory(project=project)
        SetItemFactory(result_set=result_set)
        client.force_login(user)

        response = client.get(
            reverse("research:set-detail", args=[project.id, result_set.id])
        )

        content = response.content.decode()
        assert "Ignore pages like this" in content
        assert 'list="ignore-reasons"' in content

    def test_set_detail_has_make_ignore_rule(self, client):
        user, project = self._project()
        result_set = ResultSetFactory(project=project)
        SetItemFactory(result_set=result_set)
        client.force_login(user)

        response = client.get(
            reverse("research:set-detail", args=[project.id, result_set.id])
        )

        assert "Make ignore rule" in response.content.decode()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --group test pytest tests/research/test_rule_views.py -q --no-cov`
Expected: FAIL with `NoReverseMatch`.

- [ ] **Step 3: Implement the rule views**

`research/views.py` helpers:

```python
def _spec_from_rule_form(post) -> QuerySpec:
    document_type = (post.get("document_type") or "all").strip()
    if document_type not in {"agenda", "minutes"}:
        document_type = "all"
    return QuerySpec(
        text=(post.get("text") or "").strip(),
        meeting_name=(post.get("meeting_name") or "").strip(),
        municipality_ids=_uuid_values(post.get("municipality")),
        states=_split_csv(post.get("states")),
        date_from=_date_value(post.get("date_from")),
        date_to=_date_value(post.get("date_to")),
        document_type=document_type,
    )
```

`IgnoreRuleListView`:
- owner-scoped project; rules ordered `-created`; render `research/rules/list.html`.

`IgnoreRuleCreateView`:
- GET: owner-scoped project; `page` param -> owner-scoped `SetItem` (through project sets) to prefill `meeting_name`/`document_type`; `lens` param -> `Lens.from_querydict` to prefill text/facets/dispositions excluded. Render form with initial context.
- POST: `spec = _spec_from_rule_form(request.POST)`; `action = request.POST.get("action")`.
  - `action == "preview"`: `preview = preview_rule(project, spec)`; render form with preview.
  - `action == "save"`: name required; create `IgnoreRule(project, name, predicate=spec.canonical(), reason=reason.strip())`; `AuditEvent(verb="rule.created", payload={"name", "predicate", "reason"})`; enqueue `apply_rule_to_project_task(rule.id)` on the research queue; redirect to `rule-list`.

`IgnoreRuleToggleView`:
- owner-scoped rule; target active = not rule.is_active; capture before; set; save; audit `rule.changed` payload `{"before": ..., "after": ...}`; enqueue `apply_rule_to_project_task(rule.id)` if now active else `revert_rule_task(rule.id)`; redirect.

`IgnoreRuleDeleteView`:
- owner-scoped rule; if active, `revert_rule(rule, actor=request.user)` synchronously; audit `rule.changed` payload `{"deleted": True, "name": rule.name}`; delete; redirect.

`research/tasks.py`:

```python
def apply_rule_to_project_task(rule_id) -> None:
    from .models import IgnoreRule
    from .rules import apply_rule_to_project

    apply_rule_to_project(IgnoreRule.objects.get(pk=rule_id))


def revert_rule_task(rule_id) -> None:
    from .models import IgnoreRule
    from .rules import revert_rule

    revert_rule(IgnoreRule.objects.get(pk=rule_id))
```

`research/urls.py`:

```python
path("<uuid:project>/rules/", views.IgnoreRuleListView.as_view(), name="rule-list"),
path(
    "<uuid:project>/rules/new/",
    views.IgnoreRuleCreateView.as_view(),
    name="rule-create",
),
path(
    "<uuid:project>/rules/<uuid:rule>/toggle/",
    views.IgnoreRuleToggleView.as_view(),
    name="rule-toggle",
),
path(
    "<uuid:project>/rules/<uuid:rule>/delete/",
    views.IgnoreRuleDeleteView.as_view(),
    name="rule-delete",
),
```

- [ ] **Step 4: Build the templates and entry points**

`templates/research/rules/list.html`: list rules with name, reason, active badge, predicate summary, Preview/Toggle/Delete forms, and a "New rule" link.

`templates/research/rules/form.html`: form fields `name`, `reason`, `text`, `meeting_name`, `states`, `municipality`, `date_from`, `date_to`, `document_type`; two submit buttons `action=preview` and `action=save`; render `preview.counts`, `preview.total`, and `preview.samples` when present.

`templates/research/partials/item.html`: add an "Ignore pages like this" link to `research:rule-create` with `?page={{ item.id }}`; add `<datalist id="ignore-reasons">` and `list="ignore-reasons"` on the reason input.

`templates/research/partials/review_item.html`: add the same datalist + `list` attribute.

`templates/research/set_detail.html`: add a "Make ignore rule" link to `research:rule-create` with `?lens={{ querystring|urlencode }}` (next to the lens bar).

`templates/research/project_detail.html`: add a "Rules" link in the header.

`ignore_reasons` context: add to `SetDetailView`, `DispositionView`, `UndoView`, `ReviewView`:

```python
ignore_reasons = list(
    SetItem.objects.filter(result_set__project=result_set.project)
    .exclude(ignore_reason="")
    .values_list("ignore_reason", flat=True)
    .distinct()[:20]
)
```

- [ ] **Step 5: Run the tests**

Run: `uv run --group test pytest tests/research -q --no-cov`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add research/views.py research/urls.py research/tasks.py templates/research tests/research/test_rule_views.py
git commit -m "feat: add ignore rule management UI"
```

---

## Self-Review Checklist

- [ ] **Spec coverage:** ignore rules (preview, apply, revert, precedence), freeze lens (copy/reset dispositions, set.derived), check new matches (modified cutoff, only new rows, rules on new rows, set.new_matches) and rule management UI are implemented. Bulk coding, codebook/memos, methods/export and notebook migration remain in 1D/1E.
- [ ] **Placeholder scan:** no "TBD", "TODO", "similar to Task N", or unspecified error handling.
- [ ] **Type consistency:** `preview_rule` shape (`counts`/`total`/`samples`) matches the rule form; `apply_active_rules_for_set` returns `dict[str,int]` for audit payloads; `freeze_lens` stores `copy_dispositions` in the child query so `materialize_lens_set` reads it; `check_for_new_matches` returns an `AuditEvent`.
- [ ] **Review Focus:** each of the seven lines has a named test in Tasks 1–4.
