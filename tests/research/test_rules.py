from unittest.mock import patch

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from research.audit import record_event
from research.models import AuditEvent, IgnoreRule, ResultSet
from research.rules import (
    apply_active_rules_for_set,
    apply_rule_to_project,
    preview_rule,
    revert_rule,
)
from research.tasks import apply_rule_to_project_task, revert_rule_task
from searches.queryspec import QuerySpec, build_where
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

    def test_empty_predicate_returns_nothing(self):
        project, *_ = _project_with_sets()
        preview = preview_rule(project, QuerySpec())
        assert preview == {"counts": [], "total": 0, "samples": []}

    def test_text_preview_reports_database_error(self):
        project, *_ = _project_with_sets()

        preview = preview_rule(project, QuerySpec(text="housing"))

        assert preview["counts"] == []
        assert preview["total"] == 0
        assert preview["samples"] == []
        assert "error" in preview

    def test_build_where_text_and_meeting_name_use_paradedb(self):
        where, params = build_where(QuerySpec(text="housing"), alias="mp")
        assert "mp.id @@@ paradedb.parse(%s)" in where
        assert params == ["text:(housing)"]

        where, params = build_where(QuerySpec(meeting_name="council"), alias="mp")
        assert "mp.id @@@ paradedb.parse(%s)" in where
        assert params == ["meeting_name:(council)"]

    def test_text_preview_runs_against_pg_search_when_available(self):
        with connection.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_extension WHERE extname = 'pg_search'")
            if cur.fetchone() is None:
                pytest.skip("pg_search extension is not installed")

        project, *_ = _project_with_sets()
        preview = preview_rule(project, QuerySpec(text="housing"))

        assert "error" not in preview


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

    def test_counts_match_rows_actually_updated(self):
        project, first, second, ca_item, _, _ = _project_with_sets()
        rule = IgnoreRule.objects.create(
            project=project,
            name="California",
            predicate=QuerySpec(states=("CA",)).canonical(),
        )
        before_second = second.modified
        stale_preview = {
            "counts": [
                {"result_set_id": second.id, "name": "Second", "count": 99},
            ],
            "total": 99,
            "samples": [],
        }

        with patch("research.rules.preview_rule", return_value=stale_preview):
            event = apply_rule_to_project(rule)

        ca_item.refresh_from_db()
        second.refresh_from_db()
        assert ca_item.disposition == "i"
        assert event is not None
        assert event.payload["counts"] == {str(first.id): 1}
        assert event.payload["count"] == 1
        assert second.modified == before_second

    def test_empty_predicate_is_a_noop(self):
        project, _, _, ca_item, _, _ = _project_with_sets()
        rule = IgnoreRule.objects.create(
            project=project,
            name="Everything",
            predicate={},
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
        assert event.payload["counts"] == {}
        assert event.payload["count"] == 0

    def test_reverts_owned_rows_to_unreviewed(self):
        project, first, second, ca_item, _, _ = _project_with_sets()
        rule = IgnoreRule.objects.create(
            project=project,
            name="California",
            predicate=QuerySpec(states=("CA",)).canonical(),
            reason="Out of state",
        )
        apply_rule_to_project(rule)
        before_first = ResultSet.objects.get(pk=first.id).modified
        before_second = second.modified

        event = revert_rule(rule)

        ca_item.refresh_from_db()
        first.refresh_from_db()
        second.refresh_from_db()
        assert ca_item.disposition == "u"
        assert ca_item.disposition_by_rule is None
        assert ca_item.ignore_reason == ""
        assert ca_item.last_event == event
        assert event.verb == "rule.reverted"
        assert event.payload["counts"] == {str(first.id): 1}
        assert event.payload["count"] == 1
        assert first.modified > before_first
        assert second.modified == before_second


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
        rule = IgnoreRule.objects.create(
            project=project,
            name="California",
            predicate=QuerySpec(states=("CA",)).canonical(),
        )
        event = record_event(project, "set.new_matches", result_set=first)
        ca_item.added_via = "new_matches"
        ca_item.last_event = event
        ca_item.save(update_fields=["added_via", "last_event"])

        counts = apply_active_rules_for_set(first, event=event, only_new=True)

        ca_item.refresh_from_db()
        older.refresh_from_db()
        assert counts == {str(rule.id): 1}
        assert ca_item.disposition == "i"
        assert older.disposition == "u"

    def test_only_new_does_not_reprocess_prior_event_rows(self):
        project, first, _, ca_item, _, _ = _project_with_sets()
        rule = IgnoreRule.objects.create(
            project=project,
            name="California",
            predicate=QuerySpec(states=("CA",)).canonical(),
        )
        first_event = record_event(project, "set.new_matches", result_set=first)
        ca_item.added_via = "new_matches"
        ca_item.last_event = first_event
        ca_item.save(update_fields=["added_via", "last_event"])
        apply_active_rules_for_set(first, event=first_event, only_new=True)
        ca_item.refresh_from_db()
        assert ca_item.disposition == "i"

        # A manual override leaves the row unreviewed but still tied to the
        # first event; the next check must not re-ignore it.
        ca_item.disposition = "u"
        ca_item.disposition_by_rule = None
        ca_item.ignore_reason = ""
        ca_item.save()
        new_item = SetItemFactory(
            result_set=first, page=MeetingPageFactory(document=ca_item.page.document)
        )
        second_event = record_event(project, "set.new_matches", result_set=first)
        new_item.added_via = "new_matches"
        new_item.last_event = second_event
        new_item.save(update_fields=["added_via", "last_event"])

        counts = apply_active_rules_for_set(first, event=second_event, only_new=True)

        ca_item.refresh_from_db()
        new_item.refresh_from_db()
        assert counts == {str(rule.id): 1}
        assert ca_item.disposition == "u"
        assert ca_item.last_event == first_event
        assert new_item.disposition == "i"

    def test_skips_empty_predicate(self):
        project, first, _, ca_item, _, _ = _project_with_sets()
        IgnoreRule.objects.create(
            project=project,
            name="Everything",
            predicate={},
        )
        event = record_event(project, "set.materialized", result_set=first)

        counts = apply_active_rules_for_set(first, event=event)

        ca_item.refresh_from_db()
        assert counts == {}
        assert ca_item.disposition == "u"


@pytest.mark.django_db
class TestRuleTasks:
    def test_apply_task_passes_actor_to_event(self):
        project, first, _, ca_item, _, _ = _project_with_sets()
        actor = UserFactory()
        rule = IgnoreRule.objects.create(
            project=project,
            name="California",
            predicate=QuerySpec(states=("CA",)).canonical(),
        )

        apply_rule_to_project_task(rule.id, actor.id)

        ca_item.refresh_from_db()
        event = AuditEvent.objects.get(verb="rule.applied")
        assert ca_item.disposition == "i"
        assert event.actor == actor

    def test_revert_task_passes_actor_to_event(self):
        project, first, _, ca_item, _, _ = _project_with_sets()
        actor = UserFactory()
        rule = IgnoreRule.objects.create(
            project=project,
            name="California",
            predicate=QuerySpec(states=("CA",)).canonical(),
        )
        apply_rule_to_project(rule)

        revert_rule_task(rule.id, actor.id)

        ca_item.refresh_from_db()
        event = AuditEvent.objects.get(verb="rule.reverted")
        assert ca_item.disposition == "u"
        assert event.actor == actor

    def test_apply_task_skips_rule_deactivated_after_enqueue(self):
        project, first, _, ca_item, _, _ = _project_with_sets()
        rule = IgnoreRule.objects.create(
            project=project,
            name="California",
            predicate=QuerySpec(states=("CA",)).canonical(),
        )
        rule.is_active = False
        rule.save(update_fields=["is_active", "modified"])

        apply_rule_to_project_task(rule.id)

        ca_item.refresh_from_db()
        assert ca_item.disposition == "u"
        assert not AuditEvent.objects.filter(verb="rule.applied").exists()

    def test_deleted_rule_tasks_return_cleanly(self):
        project, *_ = _project_with_sets()
        rule = IgnoreRule.objects.create(
            project=project,
            name="California",
            predicate=QuerySpec(states=("CA",)).canonical(),
        )
        rule_id = rule.id
        rule.delete()

        apply_rule_to_project_task(rule_id)
        revert_rule_task(rule_id)

        assert not AuditEvent.objects.filter(
            verb__in=["rule.applied", "rule.reverted"]
        ).exists()


@pytest.mark.django_db
class TestStatementTimeout:
    def test_apply_rule_to_project_sets_statement_timeout(self):
        project, _, _, _, _, _ = _project_with_sets()
        rule = IgnoreRule.objects.create(
            project=project,
            name="California",
            predicate=QuerySpec(states=("CA",)).canonical(),
        )

        with CaptureQueriesContext(connection) as queries:
            apply_rule_to_project(rule)

        assert any(
            "set_config('statement_timeout'" in query["sql"]
            for query in queries.captured_queries
        )

    def test_revert_rule_sets_statement_timeout(self):
        project, _, _, _, _, _ = _project_with_sets()
        rule = IgnoreRule.objects.create(
            project=project,
            name="California",
            predicate=QuerySpec(states=("CA",)).canonical(),
        )
        apply_rule_to_project(rule)

        with CaptureQueriesContext(connection) as queries:
            revert_rule(rule)

        assert any(
            "set_config('statement_timeout'" in query["sql"]
            for query in queries.captured_queries
        )

    def test_apply_rule_to_set_sets_statement_timeout(self):
        project, first, _, _, _, _ = _project_with_sets()
        IgnoreRule.objects.create(
            project=project,
            name="California",
            predicate=QuerySpec(states=("CA",)).canonical(),
        )
        event = record_event(project, "set.materialized", result_set=first)

        with CaptureQueriesContext(connection) as queries:
            apply_active_rules_for_set(first, event=event)

        assert any(
            "set_config('statement_timeout'" in query["sql"]
            for query in queries.captured_queries
        )
