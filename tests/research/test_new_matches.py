from unittest.mock import patch

import pytest
from django.utils import timezone

from research.models import ResultSet
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
