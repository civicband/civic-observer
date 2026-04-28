"""Tests for searches.quickwit_client module."""

from unittest.mock import MagicMock, patch

import pytest

from searches.quickwit_client import (
    create_index,
    delete_index,
    execute_search,
    execute_search_elasticsearch_compat,
    get_index_stats,
    health_check,
    ingest_documents,
)


@pytest.fixture(autouse=True)
def override_settings(settings):
    settings.QUICKWIT_URL = "http://localhost:7280/api/v1"
    settings.QUICKWIT_INDEX_ID = "meeting_pages"
    settings.QUICKWIT_TIMEOUT = 30


class TestCreateIndex:
    @patch("subprocess.run")
    def test_create_index_success(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="Index created\n", stderr=""
        )
        result = create_index()
        assert result is not None
        assert result["success"] is True

    @patch("subprocess.run")
    def test_create_index_already_exists(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="Error: index already exists"
        )
        result = create_index()
        assert result is not None
        assert result["success"] is True
        assert result.get("exists") is True

    @patch("subprocess.run")
    def test_create_index_file_not_found(self, mock_run):
        mock_run.side_effect = FileNotFoundError
        result = create_index()
        assert result is None

    @patch("subprocess.run")
    def test_create_index_timeout(self, mock_run):
        import subprocess

        mock_run.side_effect = subprocess.TimeoutExpired(cmd="quickwit", timeout=60)
        result = create_index()
        assert result is not None
        assert result["success"] is False


class TestDeleteIndex:
    @patch("subprocess.run")
    def test_delete_index_success(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="Index deleted\n", stderr=""
        )
        result = delete_index()
        assert result is not None
        assert result["success"] is True

    @patch("subprocess.run")
    def test_delete_index_file_not_found(self, mock_run):
        mock_run.side_effect = FileNotFoundError
        result = delete_index()
        assert result is None


class TestIngestDocuments:
    @patch("searches.quickwit_client.httpx.post")
    def test_ingest_documents_json_format(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 202
        mock_response.json.return_value = {"numDocs": 5, "numBytes": 1234}
        mock_response.content = b'{"numDocs": 5}'
        mock_post.return_value = mock_response

        documents = [
            {"id": "1", "text": "hello world"},
            {"id": "2", "text": "test document"},
        ]
        result = ingest_documents(documents, input_format="json")
        assert result == {"numDocs": 5}
        mock_post.assert_called_once()

    @patch("searches.quickwit_client.httpx.post")
    def test_ingest_documents_http_error(self, mock_post):
        import httpx

        mock_post.side_effect = httpx.ConnectError("Connection refused")

        documents = [{"id": "1", "text": "hello"}]
        result = ingest_documents(documents, input_format="json")
        assert "error" in result


class TestExecuteSearch:
    @patch("searches.quickwit_client.httpx.post")
    def test_execute_search_basic(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "num_hits": 2,
            "hits": [
                {"text": "hello world", "score": 0.5},
                {"text": "hello there", "score": 0.3},
            ],
        }
        mock_post.return_value = mock_response

        result = execute_search("hello")
        assert result["num_hits"] == 2
        assert len(result["hits"]) == 2

    @patch("searches.quickwit_client.httpx.post")
    def test_execute_search_with_filters(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"num_hits": 1, "hits": []}
        mock_post.return_value = mock_response

        execute_search(
            "budget",
            filters={"municipality_id": ["1", "2"]},
            sort_by="meeting_date",
            sort_order="asc",
            limit=10,
            offset=5,
        )
        mock_post.assert_called_once()


class TestExecuteSearchElasticsearchCompat:
    @patch("searches.quickwit_client.httpx.post")
    def test_es_compat_search_basic(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "hits": {
                "total": {"value": 5},
                "hits": [
                    {
                        "_id": "1",
                        "_score": 1.0,
                        "_source": {"id": "1", "text": "police budget"},
                    },
                ],
            }
        }
        mock_post.return_value = mock_response

        result = execute_search_elasticsearch_compat("police")
        assert result["hits"]["total"]["value"] == 5
        assert len(result["hits"]["hits"]) == 1

    @patch("searches.quickwit_client.httpx.post")
    def test_es_compat_search_with_bool_query(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"hits": {"total": {"value": 0}, "hits": []}}
        mock_post.return_value = mock_response

        execute_search_elasticsearch_compat(
            query_text="housing",
            filters=[{"terms": {"state": ["CA"]}}],
            should=[{"query_string": {"query": "policy", "fields": ["meeting_name"]}}],
            sort_by=[{"meeting_date": "desc"}],
        )
        call_args = mock_post.call_args
        body = call_args.kwargs.get("json", call_args[1].get("json"))
        assert "bool" in body["query"]
        assert "must" in body["query"]["bool"]
        assert "filter" in body["query"]["bool"]
        assert "should" in body["query"]["bool"]

    @patch("searches.quickwit_client.httpx.post")
    def test_es_compat_search_match_all(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"hits": {"total": {"value": 10}, "hits": []}}
        mock_post.return_value = mock_response

        execute_search_elasticsearch_compat(query_text="")
        call_args = mock_post.call_args
        body = call_args.kwargs.get("json", call_args[1].get("json"))
        assert body["query"] == {"match_all": {}}

    @patch("searches.quickwit_client.httpx.post")
    def test_es_compat_search_http_error(self, mock_post):
        import httpx

        mock_post.side_effect = httpx.ConnectError("Connection refused")

        result = execute_search_elasticsearch_compat("test")
        assert result["hits"]["total"]["value"] == 0
        assert result["hits"]["hits"] == []


class TestGetIndexStats:
    @patch("searches.quickwit_client.httpx.get")
    def test_get_index_stats_success(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "index_id": "meeting_pages",
            "num_docs": 1000,
        }
        mock_get.return_value = mock_response

        result = get_index_stats()
        assert result["index_id"] == "meeting_pages"
        assert result["num_docs"] == 1000

    @patch("searches.quickwit_client.httpx.get")
    def test_get_index_stats_http_error(self, mock_get):
        import httpx

        mock_get.side_effect = httpx.ConnectError("Connection refused")
        result = get_index_stats()
        assert result == {}


class TestHealthCheck:
    @patch("searches.quickwit_client.httpx.get")
    def test_health_check_healthy(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_get.return_value = mock_response
        assert health_check() is True

    @patch("searches.quickwit_client.httpx.get")
    def test_health_check_unhealthy(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 503
        mock_get.return_value = mock_response
        assert health_check() is False

    @patch("searches.quickwit_client.httpx.get")
    def test_health_check_connection_error(self, mock_get):
        import httpx

        mock_get.side_effect = httpx.ConnectError("Connection refused")
        assert health_check() is False
