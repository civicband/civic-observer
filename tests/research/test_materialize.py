from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from research.materialize import create_result_set, materialize_result_set
from research.models import AuditEvent, ResultSet
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


@pytest.mark.django_db
class TestSqlMaterialization:
    @patch("research.materialize.connection")
    @patch("research.materialize.settings")
    def test_sql_uses_build_where_and_conflict_clause(self, mock_settings, mock_conn):
        mock_settings.RESEARCH_USE_SQL_MATERIALIZATION = True
        mock_settings.RESEARCH_SET_MAX_ITEMS = 500_000
        mock_settings.RESEARCH_INLINE_SET_MAX_ITEMS = 1000
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_cursor.rowcount = 3

        project = ProjectFactory()
        result_set = ResultSet.objects.create(
            project=project,
            name="Housing",
            origin=ResultSet.Origin.QUERY,
            query=QuerySpec(text="housing").canonical(),
            query_hash=QuerySpec(text="housing").query_hash(),
        )

        materialize_result_set(result_set.id)

        sql = mock_cursor.execute.call_args[0][0]
        assert "INSERT INTO research_setitem" in sql
        assert "paradedb.score(mp.id)" in sql
        assert "ON CONFLICT (result_set_id, page_id) DO NOTHING" in sql
        assert "mp.id @@@ paradedb.parse(%s)" in sql
