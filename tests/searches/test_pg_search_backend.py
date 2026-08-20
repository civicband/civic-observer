"""
Tests for PgSearchBackend.

Since pg_search operators (|||, &&&, pdb.score, pdb.snippet) aren't available
in test databases without the ParadeDB extension, these tests mock the
database cursor to verify SQL construction and result parsing.
"""

from datetime import date
from unittest.mock import MagicMock, patch

from searches.search_backends import (
    HEADLINE_START_TAG,
    HEADLINE_STOP_TAG,
    SNIPPET_MAX_CHARS,
    PgSearchBackend,
)


class TestPgSearchBackendName:
    def test_backend_name(self):
        backend = PgSearchBackend()
        assert backend.get_backend_name() == "pg_search"


class TestPgSearchBackendSearch:
    @patch("searches.search_backends.connection")
    def test_search_with_text_query(self, mock_conn):
        """Test that a text query produces the correct SQL with ||| operator."""
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_cursor.fetchall.return_value = []

        backend = PgSearchBackend()
        results, total = backend.search(query_text="housing", limit=10, offset=0)

        assert results == []
        assert total == 0

        # Verify SQL was executed
        mock_cursor.execute.assert_called_once()
        sql = mock_cursor.execute.call_args[0][0]
        params = mock_cursor.execute.call_args[0][1]

        assert "text ||| %s" in sql
        assert "pdb.snippet" in sql
        assert "pdb.score(id) DESC" in sql
        # Params: snippet_start_tag, snippet_stop_tag, max_chars, query_text, limit, offset
        assert params[0] == HEADLINE_START_TAG
        assert params[1] == HEADLINE_STOP_TAG
        assert params[2] == SNIPPET_MAX_CHARS
        assert params[3] == "housing"
        assert params[4] == 10
        assert params[5] == 0

    @patch("searches.search_backends.connection")
    def test_search_without_text_query(self, mock_conn):
        """Test that empty query omits text predicate and uses NULL snippet."""
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_cursor.fetchall.return_value = []

        backend = PgSearchBackend()
        backend.search(query_text="", limit=10, offset=0)

        sql = mock_cursor.execute.call_args[0][0]
        params = mock_cursor.execute.call_args[0][1]

        assert "text |||" not in sql
        assert "NULL AS snippet" in sql
        assert "ORDER BY meeting_date DESC, id" in sql
        # No snippet params, just limit and offset
        assert params == [10, 0]

    @patch("searches.search_backends.connection")
    def test_search_with_meeting_name_query(self, mock_conn):
        """Test that meeting_name_query adds a filter clause."""
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_cursor.fetchall.return_value = []

        backend = PgSearchBackend()
        backend.search(query_text="budget", meeting_name_query="council")

        sql = mock_cursor.execute.call_args[0][0]
        params = mock_cursor.execute.call_args[0][1]

        assert "meeting_name ||| %s" in sql
        assert "council" in params

    @patch("searches.search_backends.connection")
    def test_search_with_municipality_queryset(self, mock_conn):
        """Test municipality filtering with a queryset-like object."""
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_cursor.fetchall.return_value = []

        # Create a mock queryset
        mock_qs = MagicMock()
        mock_qs.values_list.return_value = [1, 2, 3]

        backend = PgSearchBackend()
        backend.search(query_text="", municipalities=mock_qs)

        sql = mock_cursor.execute.call_args[0][0]
        assert "municipality_id = ANY(%s)" in sql

    @patch("searches.search_backends.connection")
    def test_search_with_states_filter(self, mock_conn):
        """Test state filtering."""
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_cursor.fetchall.return_value = []

        backend = PgSearchBackend()
        backend.search(query_text="", states=["CA", "OR"])

        sql = mock_cursor.execute.call_args[0][0]
        params = mock_cursor.execute.call_args[0][1]

        assert "state = ANY(%s)" in sql
        assert ["CA", "OR"] in params

    @patch("searches.search_backends.connection")
    def test_search_with_date_filters(self, mock_conn):
        """Test date range filtering."""
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_cursor.fetchall.return_value = []

        d_from = date(2024, 1, 1)
        d_to = date(2024, 12, 31)

        backend = PgSearchBackend()
        backend.search(query_text="", date_from=d_from, date_to=d_to)

        sql = mock_cursor.execute.call_args[0][0]
        params = mock_cursor.execute.call_args[0][1]

        assert "meeting_date >= %s" in sql
        assert "meeting_date <= %s" in sql
        assert d_from in params
        assert d_to in params

    @patch("searches.search_backends.connection")
    def test_search_with_document_type(self, mock_conn):
        """Test document type filtering."""
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_cursor.fetchall.return_value = []

        backend = PgSearchBackend()
        backend.search(query_text="", document_type="agenda")

        sql = mock_cursor.execute.call_args[0][0]
        params = mock_cursor.execute.call_args[0][1]

        assert "document_type = %s" in sql
        assert "agenda" in params

    @patch("searches.search_backends.connection")
    def test_search_document_type_all_ignored(self, mock_conn):
        """Test that document_type='all' is ignored."""
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_cursor.fetchall.return_value = []

        backend = PgSearchBackend()
        backend.search(query_text="", document_type="all")

        sql = mock_cursor.execute.call_args[0][0]
        assert "document_type = %s" not in sql

    @patch("searches.search_backends.connection")
    def test_search_pagination(self, mock_conn):
        """Test limit and offset are applied."""
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_cursor.fetchall.return_value = []

        backend = PgSearchBackend()
        backend.search(query_text="test", limit=20, offset=40)

        params = mock_cursor.execute.call_args[0][1]
        # Last two params are always limit and offset
        assert params[-2] == 20
        assert params[-1] == 40


class TestPgSearchBackendRowToDict:
    def test_row_to_dict_maps_all_fields(self):
        """Test that _row_to_dict produces the expected dict shape."""
        import uuid

        row = (
            "page-123",  # id
            uuid.uuid4(),  # document_id
            5,  # page_number
            "Some text",  # text
            "/img.png",  # page_image
            42,  # municipality_id
            "berkeley.ca",  # municipality_subdomain
            "Berkeley",  # municipality_name
            "CA",  # state
            "CityCouncil",  # meeting_name
            date(2024, 3, 15),  # meeting_date
            "agenda",  # document_type
            "<mark>match</mark>",  # snippet
            100,  # total_count (not in dict)
        )

        result = PgSearchBackend._row_to_dict(row)

        assert result["id"] == "page-123"
        assert result["document_id"] == str(row[1])
        assert result["page_number"] == 5
        assert result["text"] == "Some text"
        assert result["page_image"] == "/img.png"
        assert result["municipality_id"] == "42"
        assert result["subdomain"] == "berkeley.ca"
        assert result["municipality_name"] == "Berkeley"
        assert result["state"] == "CA"
        assert result["meeting_name"] == "CityCouncil"
        assert result["meeting_date"] == date(2024, 3, 15)
        assert result["document_type"] == "agenda"
        assert result["snippet"] == "<mark>match</mark>"
        assert result["civic_band_table_name"] == "agendas"

    def test_row_to_dict_minutes_table_name(self):
        """Test civic_band_table_name for minutes documents."""
        import uuid

        row = (
            "page-456",
            uuid.uuid4(),
            1,
            "text",
            "",
            1,
            "oakland.ca",
            "Oakland",
            "CA",
            "Council",
            date(2024, 1, 1),
            "minutes",
            None,
            50,
        )

        result = PgSearchBackend._row_to_dict(row)
        assert result["civic_band_table_name"] == "minutes"
        assert result["snippet"] is None

    @patch("searches.search_backends.connection")
    def test_search_returns_parsed_results(self, mock_conn):
        """Test that search() returns correctly parsed results with total count."""
        import uuid

        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        doc_id = uuid.uuid4()
        mock_cursor.fetchall.return_value = [
            (
                "page-1",
                doc_id,
                1,
                "Housing text",
                "/img.png",
                10,
                "city.ca",
                "City",
                "CA",
                "Council",
                date(2024, 1, 1),
                "agenda",
                "<mark>Housing</mark>",
                42,
            ),
            (
                "page-2",
                doc_id,
                2,
                "More housing",
                "/img2.png",
                10,
                "city.ca",
                "City",
                "CA",
                "Council",
                date(2024, 1, 1),
                "agenda",
                "<mark>housing</mark>",
                42,
            ),
        ]

        backend = PgSearchBackend()
        results, total = backend.search(query_text="housing", limit=10, offset=0)

        assert total == 42
        assert len(results) == 2
        assert results[0]["id"] == "page-1"
        assert results[0]["snippet"] == "<mark>Housing</mark>"
        assert results[1]["id"] == "page-2"


class TestSearchWithCache:
    @patch("searches.search_backends.get_cached_search_results")
    @patch("searches.search_backends.set_cached_search_results")
    @patch("searches.search_backends.connection")
    def test_cache_hit_skips_search(self, mock_conn, mock_set_cache, mock_get_cache):
        """Test that a cache hit returns cached results without querying."""
        cached_results = ([{"id": "cached-1"}], 1)
        mock_get_cache.return_value = cached_results

        backend = PgSearchBackend()
        results, total = backend.search_with_cache(query_text="test")

        assert results == [{"id": "cached-1"}]
        assert total == 1
        mock_conn.cursor.assert_not_called()

    @patch("searches.search_backends.get_cached_search_results")
    @patch("searches.search_backends.set_cached_search_results")
    @patch("searches.search_backends.connection")
    def test_cache_miss_executes_search(
        self, mock_conn, mock_set_cache, mock_get_cache
    ):
        """Test that a cache miss executes the search and caches results."""
        mock_get_cache.return_value = None

        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_cursor.fetchall.return_value = []

        backend = PgSearchBackend()
        backend.search_with_cache(query_text="test")

        mock_cursor.execute.assert_called_once()
        mock_set_cache.assert_called_once()
