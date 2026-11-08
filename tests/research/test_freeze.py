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
        child = freeze_lens(
            parent, "CA only", Lens(states=("CA",), dispositions=("u", "r", "i"))
        )

        materialize_lens_set(child.id)

        assert child.items.count() == 2
