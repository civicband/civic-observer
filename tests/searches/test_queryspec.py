import uuid
from datetime import UTC, date, datetime

import pytest

from searches.queryspec import QuerySpec, build_where
from tests.factories import MuniFactory, SearchFactory


class TestCanonical:
    def test_normalizes_whitespace_order_and_empty_document_type(self):
        spec = QuerySpec(
            text="  housing ",
            meeting_name=" council ",
            municipality_ids=(uuid.UUID(int=2), uuid.UUID(int=1)),
            states=("OR", "CA", "CA"),
            document_type="",
        )

        canonical = spec.canonical()

        assert canonical["text"] == "housing"
        assert canonical["meeting_name"] == "council"
        assert canonical["municipality_ids"] == [
            str(uuid.UUID(int=1)),
            str(uuid.UUID(int=2)),
        ]
        assert canonical["states"] == ["CA", "OR"]
        assert canonical["document_type"] == "all"
        assert canonical["date_from"] is None
        assert canonical["modified_after"] is None

    def test_dates_use_isoformat(self):
        spec = QuerySpec(
            date_from=date(2024, 1, 2),
            date_to=date(2024, 3, 4),
            modified_after=datetime(2024, 5, 6, 7, 8, 9, tzinfo=UTC),
        )

        canonical = spec.canonical()

        assert canonical["date_from"] == "2024-01-02"
        assert canonical["date_to"] == "2024-03-04"
        assert canonical["modified_after"] == "2024-05-06T07:08:09+00:00"

    def test_hash_is_stable_across_input_order(self):
        a = QuerySpec(
            text="housing",
            municipality_ids=(uuid.UUID(int=1), uuid.UUID(int=2)),
            states=("CA", "OR"),
        )
        b = QuerySpec(
            text="housing",
            municipality_ids=(uuid.UUID(int=2), uuid.UUID(int=1)),
            states=("OR", "CA"),
        )

        assert a.canonical_json() == b.canonical_json()
        assert a.query_hash() == b.query_hash()

    def test_hash_changes_when_modified_after_changes(self):
        first = QuerySpec(
            text="housing",
            modified_after=datetime(2024, 1, 1, tzinfo=UTC),
        )
        second = QuerySpec(
            text="housing",
            modified_after=datetime(2024, 1, 2, tzinfo=UTC),
        )

        assert first.query_hash() != second.query_hash()


@pytest.mark.django_db
class TestFromSearch:
    def test_copies_all_search_fields(self):
        ca = MuniFactory(state="CA")
        or_ = MuniFactory(state="OR")
        search = SearchFactory(
            search_term="  housing ",
            meeting_name_query=" council ",
            states=["CA", "OR"],
            date_from=date(2024, 1, 1),
            date_to=date(2024, 12, 31),
            document_type="agenda",
        )
        search.municipalities.add(or_, ca)

        spec = QuerySpec.from_search(search, modified_after=datetime(2024, 6, 1))

        assert spec.text == "housing"
        assert spec.meeting_name == "council"
        assert set(spec.municipality_ids) == {ca.id, or_.id}
        assert set(spec.states) == {"CA", "OR"}
        assert spec.date_from == date(2024, 1, 1)
        assert spec.date_to == date(2024, 12, 31)
        assert spec.document_type == "agenda"
        assert spec.modified_after == datetime(2024, 6, 1)

    def test_with_modified_after_returns_new_spec(self):
        spec = QuerySpec(text="housing")

        updated = spec.with_modified_after(datetime(2024, 6, 1))

        assert spec.modified_after is None
        assert updated.modified_after == datetime(2024, 6, 1)
        assert updated.text == "housing"


class TestBuildWhere:
    def test_full_spec(self):
        muni_id = uuid.UUID(int=7)
        spec = QuerySpec(
            text=" housing ",
            meeting_name=" council ",
            municipality_ids=(muni_id,),
            states=("CA",),
            date_from=date(2024, 1, 1),
            date_to=date(2024, 12, 31),
            document_type="agenda",
            modified_after=datetime(2024, 6, 1),
        )

        where, params = build_where(spec)

        assert where == ("mp.id @@@ paradedb.parse(%s) AND mp.modified >= %s")
        assert params == [
            (
                "text:(housing) AND meeting_name:(council)"
                ' AND municipality_id:"00000000-0000-0000-0000-000000000007"'
                ' AND state:"CA"'
                " AND meeting_date:[2024-01-01 TO 2024-12-31]"
                ' AND document_type:"agenda"'
            ),
            datetime(2024, 6, 1),
        ]

    def test_filter_only_query_keeps_sql_predicates(self):
        where, params = build_where(QuerySpec(states=("CA", "OR")))

        assert where == "mp.state = ANY(%s)"
        assert params == [["CA", "OR"]]

    def test_text_query_pushes_filters_into_bm25(self):
        where, params = build_where(
            QuerySpec(text="housing", states=("CA", "OR"), document_type="agenda")
        )

        assert where == "mp.id @@@ paradedb.parse(%s)"
        assert params == [
            'text:(housing) AND (state:"CA" OR state:"OR") AND document_type:"agenda"'
        ]

    def test_modified_after_stays_sql_for_text_queries(self):
        where, params = build_where(
            QuerySpec(text="housing", modified_after=datetime(2024, 6, 1))
        )

        assert where == "mp.id @@@ paradedb.parse(%s) AND mp.modified >= %s"
        assert params == ["text:(housing)", datetime(2024, 6, 1)]

    def test_empty_spec_is_true(self):
        assert build_where(QuerySpec()) == ("TRUE", [])

    def test_empty_municipalities_do_not_filter(self):
        where, params = build_where(QuerySpec(municipality_ids=()))
        assert where == "TRUE"
        assert params == []

    @pytest.mark.parametrize("document_type", ["", "all", None])
    def test_document_type_all_does_not_filter(self, document_type):
        where, params = build_where(QuerySpec(document_type=document_type))
        assert where == "TRUE"
        assert params == []

    def test_alias_can_be_removed(self):
        where, _ = build_where(QuerySpec(text="housing"), alias="")
        assert where == "id @@@ paradedb.parse(%s)"


class TestFromCanonical:
    def test_round_trips_canonical_json(self):
        spec = QuerySpec(
            text=" housing ",
            meeting_name=" council ",
            municipality_ids=(uuid.UUID(int=3),),
            states=("OR", "CA"),
            date_from=date(2024, 1, 2),
            date_to=date(2024, 3, 4),
            document_type="agenda",
            modified_after=datetime(2024, 6, 1, tzinfo=UTC),
        )
        rebuilt = QuerySpec.from_canonical(spec.canonical())
        assert rebuilt.canonical() == spec.canonical()
        assert rebuilt.query_hash() == spec.query_hash()
