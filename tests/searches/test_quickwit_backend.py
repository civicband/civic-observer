"""Tests for searches.search_backends.QuickwitBackend."""

from datetime import date
from unittest.mock import patch

import pytest

from searches.search_backends import QuickwitBackend


@pytest.fixture(autouse=True)
def override_settings(settings):
    settings.QUICKWIT_URL = "http://localhost:7280/api/v1"
    settings.QUICKWIT_INDEX_ID = "meeting_pages"
    settings.QUICKWIT_TIMEOUT = 30


@pytest.fixture
def backend():
    return QuickwitBackend()


@pytest.mark.django_db
class TestQuickwitBackendBuildQuery:
    def test_build_query_text_only(self, backend):
        query = backend._build_query("housing")
        assert query["main_query"] == "housing"

    def test_build_query_with_municipalities(self, backend):
        from tests.factories import MuniFactory

        muni1 = MuniFactory()
        muni2 = MuniFactory()
        query = backend._build_query("test", municipalities=[muni1, muni2])
        assert "filters" in query
        assert len(query["filters"]) == 1
        assert "terms" in query["filters"][0]

    def test_build_query_with_states(self, backend):
        query = backend._build_query("test", states=["CA", "OR"])
        filters = query["filters"]
        state_filter = next(
            f for f in filters if "terms" in f and "state" in f["terms"]
        )
        assert state_filter["terms"]["state"] == ["CA", "OR"]

    def test_build_query_with_date_from(self, backend):
        query = backend._build_query("test", date_from=date(2024, 1, 1))
        filters = query["filters"]
        date_filter = next(f for f in filters if "range" in f)
        assert date_filter["range"]["meeting_date"]["gte"] == "2024-01-01"

    def test_build_query_with_date_to(self, backend):
        query = backend._build_query("test", date_to=date(2024, 12, 31))
        filters = query["filters"]
        date_filter = next(f for f in filters if "range" in f)
        assert date_filter["range"]["meeting_date"]["lte"] == "2024-12-31"

    def test_build_query_with_document_type(self, backend):
        query = backend._build_query("test", document_type="agenda")
        filters = query["filters"]
        doc_filter = next(f for f in filters if "term" in f)
        assert doc_filter["term"]["document_type"] == "agenda"

    def test_build_query_document_type_all_ignored(self, backend):
        query = backend._build_query("test", document_type="all")
        assert "filters" not in query or not any(
            "term" in f for f in query.get("filters", [])
        )

    def test_build_query_with_meeting_name(self, backend):
        query = backend._build_query("test", meeting_name_query="city council")
        assert "should" in query

    def test_build_query_combined_should_and_text(self, backend):
        query = backend._build_query("housing", meeting_name_query="council")
        assert "should" in query
        assert len(query["should"]) == 2

    def test_build_query_empty_returns_empty(self, backend):
        query = backend._build_query("")
        assert query == {}


@pytest.mark.django_db
class TestQuickwitBackendSearch:
    @patch("searches.search_backends.execute_search_elasticsearch_compat")
    def test_search_returns_results(self, mock_search, backend):
        mock_search.return_value = {
            "hits": {
                "total": {"value": 2},
                "hits": [
                    {
                        "_source": {
                            "_source": {
                                "id": "1",
                                "page_number": 1,
                                "text": "housing policy",
                                "page_image": "img1.png",
                                "meeting_name": "City Council",
                                "meeting_date": "2024-01-01",
                                "document_type": "agenda",
                                "municipality_id": "1",
                                "municipality_subdomain": "test-city",
                                "municipality_name": "Test City",
                                "state": "CA",
                            },
                            "_score": 1.0,
                        }
                    }
                ],
            }
        }

        results, total = backend.search("housing")
        assert total == 2
        assert len(results) == 1
        assert results[0]["id"] == "1"

    @patch("searches.search_backends.execute_search_elasticsearch_compat")
    def test_search_with_pagination(self, mock_search, backend):
        mock_search.return_value = {"hits": {"total": {"value": 0}, "hits": []}}
        backend.search("test", limit=50, offset=10)
        mock_search.assert_called_once()
        call_kwargs = mock_search.call_args.kwargs
        assert call_kwargs["limit"] == 50
        assert call_kwargs["offset"] == 10


class TestQuickwitBackendHitToDict:
    def test_hit_to_dict_unwraps_double_source(self, backend):
        hit = {
            "_source": {
                "_source": {
                    "id": "1",
                    "page_number": 5,
                    "text": "test text",
                    "page_image": "img.png",
                    "meeting_name": "Meeting",
                    "meeting_date": "2024-01-01",
                    "document_type": "agenda",
                    "municipality_id": "1",
                    "municipality_subdomain": "test",
                    "municipality_name": "Test",
                    "state": "CA",
                    "document_id": "doc1",
                },
                "_score": 1.0,
            }
        }
        result = backend._hit_to_dict(hit)
        assert result["id"] == "1"
        assert result["page_number"] == 5
        assert result["text"] == "test text"
        assert result["state"] == "CA"

    def test_hit_to_dict_handles_single_source(self, backend):
        hit = {
            "_source": {
                "id": "2",
                "page_number": 10,
                "text": "direct text",
                "page_image": "img2.png",
                "meeting_name": "Direct Meeting",
                "meeting_date": "2024-02-02",
                "document_type": "minutes",
                "municipality_id": "2",
                "municipality_subdomain": "direct",
                "municipality_name": "Direct City",
                "state": "OR",
                "document_id": "doc2",
            },
            "_score": 0.5,
        }
        result = backend._hit_to_dict(hit)
        assert result["id"] == "2"
        assert result["page_number"] == 10
