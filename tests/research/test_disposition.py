import pytest
from django.utils import timezone

from research.browse import filter_items
from research.disposition import (
    dispose_item,
    dispose_items,
    undo_disposition_event,
)
from research.lens import Lens
from research.models import AuditEvent
from tests.factories import (
    IgnoreRuleFactory,
    ProjectFactory,
    ResultSetFactory,
    SetItemFactory,
    UserFactory,
)


@pytest.mark.django_db
class TestDisposeItem:
    def test_marks_relevant_and_records_one_event(self):
        item = SetItemFactory()
        actor = UserFactory()

        event = dispose_item(item, "r", actor=actor)
        item.refresh_from_db()

        assert item.disposition == "r"
        assert item.previous_disposition == "u"
        assert item.ignore_reason == ""
        assert item.last_event == event
        assert event.verb == "items.disposed"
        assert event.actor == actor
        assert event.result_set == item.result_set
        assert event.payload["item_ids"] == [item.id]
        assert event.payload["from"] == "u"
        assert event.payload["to"] == "r"
        assert event.payload["count"] == 1
        assert event.payload["reason"] == ""

    def test_ignore_records_reason(self):
        item = SetItemFactory()

        dispose_item(item, "i", reason="  Boilerplate  ")
        item.refresh_from_db()

        assert item.disposition == "i"
        assert item.ignore_reason == "Boilerplate"
        assert item.previous_disposition == "u"

    def test_manual_disposition_clears_rule(self):
        item = SetItemFactory()
        rule = IgnoreRuleFactory(project=item.result_set.project)
        item.disposition = "i"
        item.disposition_by_rule = rule
        item.save()

        dispose_item(item, "r")
        item.refresh_from_db()

        assert item.disposition == "r"
        assert item.disposition_by_rule is None
        assert item.ignore_reason == ""

    def test_manual_same_disposition_clears_rule(self):
        item = SetItemFactory()
        rule = IgnoreRuleFactory(project=item.result_set.project)
        item.disposition = "i"
        item.disposition_by_rule = rule
        item.save()

        event = dispose_item(item, "i")
        item.refresh_from_db()

        assert event is not None
        assert item.disposition == "i"
        assert item.disposition_by_rule is None
        assert item.previous_disposition == "i"
        assert AuditEvent.objects.filter(verb="items.disposed").count() == 1

    def test_unreviewed_clears_reason(self):
        item = SetItemFactory(disposition="i", ignore_reason="Boilerplate")

        dispose_item(item, "u")
        item.refresh_from_db()

        assert item.disposition == "u"
        assert item.ignore_reason == ""

    def test_noop_writes_no_event(self):
        item = SetItemFactory(disposition="u")

        event = dispose_item(item, "u")

        assert event is None
        assert not AuditEvent.objects.filter(verb="items.disposed").exists()

    def test_same_disposition_with_new_reason_writes_event(self):
        item = SetItemFactory(disposition="i", ignore_reason="Boilerplate")

        event = dispose_item(item, "i", reason="Off topic")

        assert event is not None
        item.refresh_from_db()
        assert item.ignore_reason == "Off topic"

    def test_invalid_disposition_raises(self):
        item = SetItemFactory()

        with pytest.raises(ValueError):
            dispose_item(item, "x")

    def test_reason_is_truncated_to_500_characters(self):
        item = SetItemFactory()

        dispose_item(item, "i", reason="x" * 600)
        item.refresh_from_db()

        assert len(item.ignore_reason) == 500

    def test_bumps_set_modified(self):
        item = SetItemFactory()
        before = item.result_set.modified

        dispose_item(item, "r")

        item.result_set.refresh_from_db()
        assert item.result_set.modified > before

    def test_missing_page_still_disposes(self):
        item = SetItemFactory()
        from meetings.models import MeetingPage

        MeetingPage.objects.filter(pk=item.page_id).delete()

        event = dispose_item(item, "r")
        item.refresh_from_db()

        assert event is not None
        assert item.disposition == "r"


@pytest.mark.django_db
class TestDisposeItems:
    def _three_items(self):
        result_set = ResultSetFactory()
        return result_set, [
            SetItemFactory(result_set=result_set, disposition="u"),
            SetItemFactory(result_set=result_set, disposition="u"),
            SetItemFactory(result_set=result_set, disposition="r"),
        ]

    def test_updates_only_lens_items(self):
        result_set, items = self._three_items()

        event, count = dispose_items(
            result_set, Lens(dispositions=("u",)), "i", reason="Boilerplate"
        )

        assert count == 2
        for item in items:
            item.refresh_from_db()
        assert items[0].disposition == "i"
        assert items[1].disposition == "i"
        assert items[2].disposition == "r"
        assert items[0].previous_disposition == "u"
        assert items[0].ignore_reason == "Boilerplate"
        assert event.payload["count"] == 2
        assert event.payload["from"] == {"u": 2}
        assert event.payload["lens"] == "disposition=u"

    def test_clears_rule_for_bulk(self):
        result_set, items = self._three_items()
        rule = IgnoreRuleFactory(project=result_set.project)
        items[0].disposition = "i"
        items[0].disposition_by_rule = rule
        items[0].save()

        dispose_items(result_set, Lens(dispositions=("i",)), "u")

        items[0].refresh_from_db()
        assert items[0].disposition == "u"
        assert items[0].disposition_by_rule is None

    def test_no_matches_writes_no_event(self):
        result_set = ResultSetFactory()

        event, count = dispose_items(result_set, Lens(dispositions=("i",)), "r")

        assert event is None
        assert count == 0
        assert not AuditEvent.objects.filter(verb="items.disposed").exists()

    def test_invalid_disposition_raises(self):
        result_set = ResultSetFactory()

        with pytest.raises(ValueError):
            dispose_items(result_set, Lens(), "x")

    def test_does_not_touch_other_sets(self):
        result_set = ResultSetFactory()
        other_set = ResultSetFactory(project=result_set.project)
        mine = SetItemFactory(result_set=result_set, disposition="u")
        other = SetItemFactory(result_set=other_set, disposition="u")

        dispose_items(result_set, Lens(dispositions=("u",)), "r")

        mine.refresh_from_db()
        other.refresh_from_db()
        assert mine.disposition == "r"
        assert other.disposition == "u"

    def test_bumps_set_modified(self):
        result_set = ResultSetFactory()
        SetItemFactory(result_set=result_set, disposition="u")
        before = result_set.modified

        dispose_items(result_set, Lens(dispositions=("u",)), "r")

        result_set.refresh_from_db()
        assert result_set.modified > before


@pytest.mark.django_db
class TestUndoDispositionEvent:
    def test_restores_previous_dispositions(self):
        result_set = ResultSetFactory()
        item_a = SetItemFactory(result_set=result_set, disposition="u")
        item_b = SetItemFactory(result_set=result_set, disposition="r")
        event, _ = dispose_items(result_set, Lens(), "i", reason="Boilerplate")

        undo, count = undo_disposition_event(event)

        item_a.refresh_from_db()
        item_b.refresh_from_db()
        assert count == 2
        assert item_a.disposition == "u"
        assert item_b.disposition == "r"
        assert item_a.previous_disposition == ""
        assert item_b.previous_disposition == ""
        assert item_a.ignore_reason == ""
        assert item_b.ignore_reason == ""
        assert item_a.disposition_by_rule is None
        assert undo.verb == "bulk.undone"
        assert undo.payload["original_event_id"] == event.id
        assert undo.payload["count"] == 2
        assert item_a.last_event == undo

    def test_does_not_touch_items_changed_after_event(self):
        result_set = ResultSetFactory()
        item = SetItemFactory(result_set=result_set, disposition="u")
        event, _ = dispose_items(result_set, Lens(), "i", reason="Boilerplate")
        dispose_item(item, "r")

        undo, count = undo_disposition_event(event)

        item.refresh_from_db()
        assert count == 0
        assert undo is None
        assert item.disposition == "r"

    def test_rejects_other_event_verbs(self):
        event = AuditEvent.objects.create(
            project=ProjectFactory(), verb="set.materialized"
        )

        with pytest.raises(ValueError):
            undo_disposition_event(event)

    def test_bumps_set_modified(self):
        result_set = ResultSetFactory()
        SetItemFactory(result_set=result_set, disposition="u")
        event, _ = dispose_items(result_set, Lens(), "r")
        before = timezone.now()

        undo_disposition_event(event)

        result_set.refresh_from_db()
        assert result_set.modified >= before


@pytest.mark.django_db
class TestFilterItems:
    def test_public_filter_matches_browse_defaults(self):
        result_set = ResultSetFactory()
        visible = SetItemFactory(result_set=result_set, disposition="u")
        ignored = SetItemFactory(result_set=result_set, disposition="i")

        assert list(filter_items(result_set, Lens())) == [visible]
        assert ignored not in list(filter_items(result_set, Lens()))
