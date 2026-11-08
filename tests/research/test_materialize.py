from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from research.materialize import create_result_set, materialize_result_set
from research.models import AuditEvent, ResultSet
from research.tasks import materialize_result_set_task
from searches.queryspec import QuerySpec
from tests.factories import (
    MeetingDocumentFactory,
    MeetingPageFactory,
    MuniFactory,
    ProjectFactory,
)


@pytest.mark.django_db
class TestCreateResultSet:
    def test_inline_materializes_and_records_audit(self, settings):
        settings.RESEARCH_INLINE_SET_MAX_ITEMS = 1000
        settings.RESEARCH_SET_MAX_ITEMS = 500_000
        muni = MuniFactory()
        doc = MeetingDocumentFactory(municipality=muni, meeting_date=date(2024, 3, 1))
        first = MeetingPageFactory(document=doc, text="housing policy")
        second = MeetingPageFactory(document=doc, text="housing budget")
        MeetingPageFactory(document=doc, text="zoning")
        project = ProjectFactory()

        result_set = create_result_set(
            project, "Housing", QuerySpec(text="housing", municipality_ids=(muni.id,))
        )

        assert result_set.status == ResultSet.Status.READY
        assert result_set.item_count == 2
        assert set(result_set.items.values_list("page_id", flat=True)) == {
            first.id,
            second.id,
        }
        event = AuditEvent.objects.get(project=project, verb="set.materialized")
        assert event.payload["item_count"] == 2
        assert event.payload["query"]["text"] == "housing"

    def test_over_ceiling_becomes_too_large(self, settings):
        settings.RESEARCH_SET_MAX_ITEMS = 1
        settings.RESEARCH_INLINE_SET_MAX_ITEMS = 1
        muni = MuniFactory()
        doc = MeetingDocumentFactory(municipality=muni)
        MeetingPageFactory(document=doc, text="housing")
        MeetingPageFactory(document=doc, text="housing")
        project = ProjectFactory()

        result_set = create_result_set(project, "Too big", QuerySpec(text="housing"))

        assert result_set.status == ResultSet.Status.TOO_LARGE
        assert result_set.item_count == 2
        assert result_set.items.count() == 0
        assert "narrow" in result_set.error.lower()

    def test_rerun_is_idempotent(self, settings):
        settings.RESEARCH_SET_MAX_ITEMS = 500_000
        settings.RESEARCH_INLINE_SET_MAX_ITEMS = 1000
        muni = MuniFactory()
        doc = MeetingDocumentFactory(municipality=muni)
        page = MeetingPageFactory(document=doc, text="housing")
        project = ProjectFactory()
        result_set = create_result_set(project, "Housing", QuerySpec(text="housing"))

        materialize_result_set(result_set.id)

        assert result_set.items.count() == 1
        assert result_set.items.get().page_id == page.id

    @patch("django_rq.get_queue")
    def test_large_set_enqueues_without_materializing(self, mock_get_queue, settings):
        settings.RESEARCH_SET_MAX_ITEMS = 10
        settings.RESEARCH_INLINE_SET_MAX_ITEMS = 1
        muni = MuniFactory()
        doc = MeetingDocumentFactory(municipality=muni)
        MeetingPageFactory(document=doc, text="housing")
        MeetingPageFactory(document=doc, text="housing")
        MeetingPageFactory(document=doc, text="housing")
        project = ProjectFactory()

        result_set = create_result_set(project, "Housing", QuerySpec(text="housing"))

        mock_get_queue.assert_called_once_with(settings.RESEARCH_QUEUE)
        mock_get_queue.return_value.enqueue.assert_called_once_with(
            materialize_result_set_task,
            result_set.id,
            job_timeout=settings.RESEARCH_JOB_TIMEOUT,
        )
        assert result_set.status == ResultSet.Status.PENDING
        assert result_set.items.count() == 0

    def test_count_failure_marks_set_failed(self, settings):
        settings.RESEARCH_SET_MAX_ITEMS = 500_000
        settings.RESEARCH_INLINE_SET_MAX_ITEMS = 1000
        project = ProjectFactory()

        with patch(
            "research.materialize.search_backends.get_search_backend"
        ) as get_backend:
            get_backend.return_value.count_matches.side_effect = RuntimeError(
                "count broke"
            )
            result_set = create_result_set(
                project, "Housing", QuerySpec(text="housing")
            )

        assert result_set.status == ResultSet.Status.FAILED
        assert "count broke" in result_set.error

    @patch("research.materialize.enqueue_materialization")
    def test_enqueue_failure_marks_set_failed(self, mock_enqueue, settings):
        settings.RESEARCH_SET_MAX_ITEMS = 10
        settings.RESEARCH_INLINE_SET_MAX_ITEMS = 1
        mock_enqueue.side_effect = RuntimeError("redis down")
        muni = MuniFactory()
        doc = MeetingDocumentFactory(municipality=muni)
        MeetingPageFactory(document=doc, text="housing")
        MeetingPageFactory(document=doc, text="housing")
        project = ProjectFactory()

        result_set = create_result_set(project, "Housing", QuerySpec(text="housing"))

        assert result_set.status == ResultSet.Status.FAILED
        assert "redis down" in result_set.error

    @patch("research.materialize._insert_items_orm")
    def test_inline_failure_marks_set_failed_without_raising(
        self, mock_insert, settings
    ):
        settings.RESEARCH_SET_MAX_ITEMS = 500_000
        settings.RESEARCH_INLINE_SET_MAX_ITEMS = 1000
        mock_insert.side_effect = RuntimeError("insert broke")
        project = ProjectFactory()

        result_set = create_result_set(project, "Housing", QuerySpec(text="housing"))

        assert result_set.status == ResultSet.Status.FAILED
        assert "insert broke" in result_set.error


@pytest.mark.django_db
class TestSqlMaterialization:
    @patch("research.materialize.connection")
    @patch("research.materialize.settings")
    def test_sql_uses_build_where_and_conflict_clause(self, mock_settings, mock_conn):
        mock_settings.RESEARCH_USE_SQL_MATERIALIZATION = True
        mock_settings.RESEARCH_SET_MAX_ITEMS = 500_000
        mock_settings.RESEARCH_INLINE_SET_MAX_ITEMS = 1000
        mock_settings.RESEARCH_STATEMENT_TIMEOUT_MS = 900_000
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        project = ProjectFactory()
        result_set = ResultSet.objects.create(
            project=project,
            name="Housing",
            origin=ResultSet.Origin.QUERY,
            query=QuerySpec(text="housing").canonical(),
            query_hash=QuerySpec(text="housing").query_hash(),
        )

        materialize_result_set(result_set.id)

        calls = mock_cursor.execute.call_args_list
        timeout_sql, timeout_params = calls[0].args
        assert "set_config('statement_timeout'" in timeout_sql
        assert timeout_params == [str(mock_settings.RESEARCH_STATEMENT_TIMEOUT_MS)]

        sql = calls[-1].args[0]
        assert "INSERT INTO research_setitem" in sql
        assert "paradedb.score(mp.id)" in sql
        assert "previous_disposition" in sql
        assert "ignore_reason" in sql
        assert "ON CONFLICT (result_set_id, page_id) DO NOTHING" in sql
        assert "mp.id @@@ paradedb.parse(%s)" in sql


@pytest.mark.django_db
class TestSqlMaterializationAgainstPostgres:
    def test_state_filtered_set_materializes_and_is_idempotent(self, settings):
        settings.RESEARCH_USE_SQL_MATERIALIZATION = True

        ca = MuniFactory(state="CA")
        ca_doc = MeetingDocumentFactory(municipality=ca)
        first = MeetingPageFactory(document=ca_doc, text="first")
        second = MeetingPageFactory(document=ca_doc, text="second")
        or_ = MuniFactory(state="OR")
        or_doc = MeetingDocumentFactory(municipality=or_)
        MeetingPageFactory(document=or_doc, text="third")
        project = ProjectFactory()

        spec = QuerySpec(states=("CA",))
        result_set = ResultSet.objects.create(
            project=project,
            name="California pages",
            origin=ResultSet.Origin.QUERY,
            query=spec.canonical(),
            query_hash=spec.query_hash(),
        )

        result_set = materialize_result_set(result_set.id)

        assert result_set.status == ResultSet.Status.READY
        assert result_set.item_count == 2
        items = list(result_set.items.all())
        assert {item.page_id for item in items} == {first.id, second.id}
        assert all(item.rank is None for item in items)
        assert all(item.previous_disposition == "" for item in items)
        assert all(item.ignore_reason == "" for item in items)

        materialize_result_set(result_set.id)

        assert result_set.items.count() == 2


@pytest.mark.django_db
def test_materialization_applies_active_rules(settings):
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
