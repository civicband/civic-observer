from datetime import date

import pytest

from research.browse import browse_set, set_facets
from research.lens import Lens
from research.models import SetItem
from tests.factories import (
    CodingFactory,
    MeetingPageFactory,
    ResultSetFactory,
    SetItemFactory,
)


@pytest.mark.django_db
class TestBrowseSet:
    def _items(self):
        result_set = ResultSetFactory()
        return result_set, [
            SetItemFactory(
                result_set=result_set,
                rank=3.0,
                page=MeetingPageFactory(text="housing"),
            ),
            SetItemFactory(
                result_set=result_set,
                rank=2.0,
                page=MeetingPageFactory(text="housing"),
            ),
            SetItemFactory(
                result_set=result_set,
                rank=1.0,
                page=MeetingPageFactory(text="housing"),
            ),
        ]

    def test_ignored_hidden_by_default(self):
        result_set, items = self._items()
        items[1].disposition = SetItem.Disposition.IGNORED
        items[1].save()
        visible, _ = browse_set(result_set, Lens())
        assert items[1] not in visible
        assert len(visible) == 2

    def test_keyset_pagination_does_not_repeat(self):
        result_set, items = self._items()
        first_page, after = browse_set(result_set, Lens(), limit=2)
        second_page, after2 = browse_set(result_set, Lens(), limit=2, after=after)
        assert first_page + second_page == items
        assert after2 is None

    def test_rank_null_sorts_last_and_paginates(self):
        result_set = ResultSetFactory()
        ranked = SetItemFactory(result_set=result_set, rank=1.0)
        unranked_a = SetItemFactory(result_set=result_set, rank=None)
        unranked_b = SetItemFactory(result_set=result_set, rank=None)
        first_page, after = browse_set(result_set, Lens(), limit=2)
        second_page, _ = browse_set(result_set, Lens(), limit=2, after=after)
        assert first_page == [ranked, unranked_a]
        assert second_page == [unranked_b]

    def test_date_order_paginates_with_nulls_last(self):
        result_set = ResultSetFactory()
        older = SetItemFactory(result_set=result_set, meeting_date=date(2024, 1, 1))
        newer = SetItemFactory(result_set=result_set, meeting_date=date(2024, 6, 1))
        undated = SetItemFactory(result_set=result_set, meeting_date=None)

        first_page, after = browse_set(result_set, Lens(order="date_desc"), limit=2)
        second_page, after2 = browse_set(
            result_set, Lens(order="date_desc"), limit=2, after=after
        )
        assert first_page == [newer, older]
        assert second_page == [undated]
        assert after2 is None

        first_page, after = browse_set(result_set, Lens(order="date_asc"), limit=2)
        second_page, _ = browse_set(
            result_set, Lens(order="date_asc"), limit=2, after=after
        )
        assert first_page == [older, newer]
        assert second_page == [undated]

    def test_state_and_disposition_filters(self):
        result_set, items = self._items()
        items[0].state = "CA"
        items[0].disposition = SetItem.Disposition.RELEVANT
        items[0].save()
        visible, _ = browse_set(result_set, Lens(states=("CA",), dispositions=("r",)))
        assert visible == [items[0]]

    def test_code_filters(self):
        result_set, items = self._items()
        code = CodingFactory(project=result_set.project, page=items[0].page).code
        assert browse_set(result_set, Lens(codes_all=(code.id,)))[0] == [items[0]]
        assert browse_set(result_set, Lens(codes_none=(code.id,)))[0] == items[1:]
        assert browse_set(result_set, Lens(uncoded=True))[0] == items[1:]

    def test_text_subsearch_uses_backend(self):
        result_set, items = self._items()
        visible, _ = browse_set(result_set, Lens(text="housing"))
        assert visible == items


@pytest.mark.django_db
class TestFacets:
    def test_counts_respect_the_lens(self):
        result_set = ResultSetFactory()
        first = SetItemFactory(
            result_set=result_set, state="CA", document_type="agenda"
        )
        second = SetItemFactory(
            result_set=result_set, state="OR", document_type="minutes"
        )
        facets = set_facets(result_set, Lens(states=("CA",)))
        assert facets["states"] == [{"state": "CA", "count": 1}]
        assert {row["document_type"] for row in facets["document_types"]} == {"agenda"}
        assert first.state == "CA" and second.state == "OR"
