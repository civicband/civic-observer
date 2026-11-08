from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from research.browse import _encode_cursor, browse_set, review_item, set_facets
from research.disposition import dispose_item
from research.lens import Lens
from research.models import SetItem
from searches.queryspec import QuerySpec
from tests.factories import (
    CodingFactory,
    MeetingDocumentFactory,
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

    def test_rank_tail_follows_page_id_not_related_ordering(self):
        result_set = ResultSetFactory()
        early_doc = MeetingDocumentFactory(meeting_date=date(2024, 1, 1))
        late_doc = MeetingDocumentFactory(meeting_date=date(2024, 6, 1))
        early_page = MeetingPageFactory(id="page-a", document=early_doc)
        late_page = MeetingPageFactory(id="page-b", document=late_doc)
        early = SetItemFactory(result_set=result_set, rank=None, page=early_page)
        late = SetItemFactory(result_set=result_set, rank=None, page=late_page)

        first_page, after = browse_set(result_set, Lens(), limit=1)
        second_page, after2 = browse_set(result_set, Lens(), limit=1, after=after)

        assert first_page == [early]
        assert second_page == [late]
        assert after2 is None

    def test_date_tiebreaker_follows_page_id_not_page_number(self):
        result_set = ResultSetFactory()
        doc = MeetingDocumentFactory(meeting_date=date(2024, 3, 1))
        page_a = MeetingPageFactory(id="page-a", document=doc, page_number=2)
        page_b = MeetingPageFactory(id="page-b", document=doc, page_number=1)
        item_a = SetItemFactory(
            result_set=result_set, meeting_date=date(2024, 3, 1), page=page_a
        )
        item_b = SetItemFactory(
            result_set=result_set, meeting_date=date(2024, 3, 1), page=page_b
        )

        first_page, after = browse_set(result_set, Lens(order="date_desc"), limit=1)
        second_page, after2 = browse_set(
            result_set, Lens(order="date_desc"), limit=1, after=after
        )

        assert first_page == [item_a]
        assert second_page == [item_b]
        assert after2 is None

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

    def test_text_subsearch_combines_set_and_lens_text(self):
        spec = QuerySpec(text="housing")
        result_set = ResultSetFactory(
            query=spec.canonical(), query_hash=spec.query_hash()
        )
        item = SetItemFactory(
            result_set=result_set, page=MeetingPageFactory(text="housing zoning")
        )
        backend = MagicMock()
        backend.search.return_value = ([{"id": item.page_id}], 1)

        with patch(
            "research.browse.search_backends.get_search_backend",
            return_value=backend,
        ):
            visible, _ = browse_set(result_set, Lens(text="zoning"))

        assert visible == [item]
        assert backend.search.call_args.kwargs["query_text"] == (
            "(housing) AND (zoning)"
        )


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


@pytest.mark.django_db
class TestReviewItem:
    def _set(self):
        result_set = ResultSetFactory()
        items = [
            SetItemFactory(result_set=result_set, rank=3.0),
            SetItemFactory(result_set=result_set, rank=2.0),
            SetItemFactory(result_set=result_set, rank=1.0),
        ]
        return result_set, items

    def test_returns_first_item_without_cursor(self):
        result_set, items = self._set()

        item, cursor = review_item(result_set, Lens())

        assert item == items[0]
        assert cursor == _encode_cursor(items[0], "rank")

    def test_after_moves_forward(self):
        result_set, items = self._set()

        item, cursor = review_item(
            result_set, Lens(), after=_encode_cursor(items[0], "rank")
        )

        assert item == items[1]
        assert cursor == _encode_cursor(items[1], "rank")

    def test_before_moves_backward(self):
        result_set, items = self._set()

        item, cursor = review_item(
            result_set, Lens(), before=_encode_cursor(items[1], "rank")
        )

        assert item == items[0]
        assert cursor == _encode_cursor(items[0], "rank")

    def test_before_from_first_item_returns_none(self):
        result_set, items = self._set()

        item, cursor = review_item(
            result_set, Lens(), before=_encode_cursor(items[0], "rank")
        )

        assert item is None
        assert cursor is None

    def test_after_from_last_item_returns_none(self):
        result_set, items = self._set()

        item, cursor = review_item(
            result_set, Lens(), after=_encode_cursor(items[-1], "rank")
        )

        assert item is None
        assert cursor is None

    def test_rank_null_forward_and_backward(self):
        result_set = ResultSetFactory()
        ranked = SetItemFactory(result_set=result_set, rank=1.0)
        unranked = SetItemFactory(result_set=result_set, rank=None)

        forward, _ = review_item(
            result_set, Lens(), after=_encode_cursor(ranked, "rank")
        )
        backward, _ = review_item(
            result_set, Lens(), before=_encode_cursor(unranked, "rank")
        )

        assert forward == unranked
        assert backward == ranked

    def test_date_asc_forward_and_backward(self):
        result_set = ResultSetFactory()
        older = SetItemFactory(result_set=result_set, meeting_date=date(2024, 1, 1))
        newer = SetItemFactory(result_set=result_set, meeting_date=date(2024, 6, 1))
        lens = Lens(order="date_asc")

        forward, _ = review_item(
            result_set, lens, after=_encode_cursor(older, "date_asc")
        )
        backward, _ = review_item(
            result_set, lens, before=_encode_cursor(newer, "date_asc")
        )

        assert forward == newer
        assert backward == older

    def test_date_null_forward_and_backward(self):
        result_set = ResultSetFactory()
        dated = SetItemFactory(result_set=result_set, meeting_date=date(2024, 1, 1))
        undated = SetItemFactory(result_set=result_set, meeting_date=None)
        lens = Lens(order="date_desc")

        forward, _ = review_item(
            result_set, lens, after=_encode_cursor(dated, "date_desc")
        )
        backward, _ = review_item(
            result_set, lens, before=_encode_cursor(undated, "date_desc")
        )

        assert forward == undated
        assert backward == dated

    def test_rejects_after_and_before_together(self):
        result_set = ResultSetFactory()

        with pytest.raises(ValueError):
            review_item(result_set, Lens(), after="1|a", before="2|b")

    def test_rejects_malformed_cursor(self):
        result_set = ResultSetFactory()

        with pytest.raises(ValueError):
            review_item(result_set, Lens(), after="not-a-cursor")

    def test_handles_missing_page(self):
        result_set = ResultSetFactory()
        item = SetItemFactory(result_set=result_set)
        from meetings.models import MeetingPage

        MeetingPage.objects.filter(pk=item.page_id).delete()

        found, _ = review_item(result_set, Lens())

        assert found is not None
        assert found == item
        assert found.page_obj is None

    def test_loads_last_event_with_item(self, django_assert_num_queries):
        result_set = ResultSetFactory()
        item = SetItemFactory(result_set=result_set)
        dispose_item(item, "r")

        found, _ = review_item(result_set, Lens())

        assert found is not None
        with django_assert_num_queries(0):
            assert found.can_undo is True
