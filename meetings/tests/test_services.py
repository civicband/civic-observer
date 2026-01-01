"""Tests for meetings services."""

from datetime import date
from unittest.mock import Mock, patch

import pytest

from meetings.services import _backfill_document_type
from municipalities.models import Muni


@pytest.mark.django_db
class TestBackfillDocumentType:
    @patch("meetings.services.httpx.Client")
    def test_backfill_with_date_range_filter(self, mock_client_class):
        """Test that date filters are added to API query params."""
        muni = Muni.objects.create(
            subdomain="test-city",
            name="Test City",
            state="CA",
        )

        # Mock the HTTP client
        mock_client = Mock()
        mock_client_class.return_value.__enter__.return_value = mock_client

        # Mock API response with no results
        mock_response = Mock()
        mock_response.json.return_value = {
            "rows": [],
            "next": None,
        }
        mock_client.get.return_value = mock_response

        # Call with date range
        start_date = date(2024, 12, 1)
        end_date = date(2025, 6, 1)
        stats, next_cursor = _backfill_document_type(
            muni=muni,
            table_name="agendas",
            document_type="agenda",
            date_range=(start_date, end_date),
        )

        # Verify API was called with date filters
        call_args = mock_client.get.call_args
        assert "date__gte" in call_args[1]["params"]
        assert call_args[1]["params"]["date__gte"] == "2024-12-01"
        assert "date__lte" in call_args[1]["params"]
        assert call_args[1]["params"]["date__lte"] == "2025-06-01"

    @patch("meetings.services.httpx.Client")
    def test_backfill_with_max_pages_limit(self, mock_client_class):
        """Test that backfill stops after max_pages."""
        muni = Muni.objects.create(
            subdomain="test-city",
            name="Test City",
            state="CA",
        )

        # Mock the HTTP client
        mock_client = Mock()
        mock_client_class.return_value.__enter__.return_value = mock_client

        # Mock API responses - 3 pages available but we'll limit to 2
        mock_response_1 = Mock()
        mock_response_1.json.return_value = {
            "rows": [
                {
                    "id": "1",
                    "meeting": "Council",
                    "date": "2024-12-01",
                    "page": 1,
                    "text": "test",
                }
            ],
            "next": "cursor_2",
        }
        mock_response_2 = Mock()
        mock_response_2.json.return_value = {
            "rows": [
                {
                    "id": "2",
                    "meeting": "Council",
                    "date": "2024-12-01",
                    "page": 2,
                    "text": "test",
                }
            ],
            "next": "cursor_3",
        }
        mock_client.get.side_effect = [mock_response_1, mock_response_2]

        # Call with max_pages=2
        stats, next_cursor = _backfill_document_type(
            muni=muni,
            table_name="agendas",
            document_type="agenda",
            max_pages=2,
        )

        # Should stop after 2 pages and return the cursor for page 3
        assert mock_client.get.call_count == 2
        assert next_cursor == "cursor_3"
        assert stats["pages_created"] == 2

    @patch("meetings.services.httpx.Client")
    def test_date_filters_preserved_across_pagination(self, mock_client_class):
        """Test that date filters are preserved when paginating."""
        muni = Muni.objects.create(
            subdomain="test-city",
            name="Test City",
            state="CA",
        )

        mock_client = Mock()
        mock_client_class.return_value.__enter__.return_value = mock_client

        # Mock two pages with date-filtered results
        mock_response_1 = Mock()
        mock_response_1.json.return_value = {
            "rows": [
                {
                    "id": "1",
                    "meeting": "Council",
                    "date": "2024-12-15",
                    "page": 1,
                    "text": "test",
                }
            ],
            "next": "cursor_2",
        }
        mock_response_2 = Mock()
        mock_response_2.json.return_value = {
            "rows": [
                {
                    "id": "2",
                    "meeting": "Council",
                    "date": "2025-01-05",
                    "page": 1,
                    "text": "test",
                }
            ],
            "next": None,
        }
        mock_client.get.side_effect = [mock_response_1, mock_response_2]

        # Call with date range
        start_date = date(2024, 12, 1)
        end_date = date(2025, 6, 1)
        stats, next_cursor = _backfill_document_type(
            muni=muni,
            table_name="agendas",
            document_type="agenda",
            date_range=(start_date, end_date),
        )

        # Verify both API calls included date filters
        assert mock_client.get.call_count == 2
        first_call = mock_client.get.call_args_list[0]
        second_call = mock_client.get.call_args_list[1]

        assert first_call[1]["params"]["date__gte"] == "2024-12-01"
        assert first_call[1]["params"]["date__lte"] == "2025-06-01"
        assert second_call[1]["params"]["date__gte"] == "2024-12-01"
        assert second_call[1]["params"]["date__lte"] == "2025-06-01"

    @patch("meetings.services.httpx.Client")
    def test_backfill_resumes_from_cursor(self, mock_client_class):
        """Test that backfill resumes from provided cursor."""
        muni = Muni.objects.create(
            subdomain="test-city",
            name="Test City",
            state="CA",
        )

        mock_client = Mock()
        mock_client_class.return_value.__enter__.return_value = mock_client

        mock_response = Mock()
        mock_response.json.return_value = {
            "rows": [],
            "next": None,
        }
        mock_client.get.return_value = mock_response

        # Call with start_cursor
        stats, next_cursor = _backfill_document_type(
            muni=muni,
            table_name="agendas",
            document_type="agenda",
            start_cursor="resume_cursor_123",
        )

        # Verify API was called with cursor
        call_args = mock_client.get.call_args
        assert call_args[1]["params"]["_next"] == "resume_cursor_123"

    @patch("meetings.services.httpx.Client")
    def test_combined_parameters(self, mock_client_class):
        """Test that start_cursor, max_pages, and date_range work together."""
        muni = Muni.objects.create(
            subdomain="test-city",
            name="Test City",
            state="CA",
        )

        mock_client = Mock()
        mock_client_class.return_value.__enter__.return_value = mock_client

        # Mock two pages - will stop at max_pages
        mock_response_1 = Mock()
        mock_response_1.json.return_value = {
            "rows": [
                {
                    "id": "1",
                    "meeting": "Council",
                    "date": "2024-12-15",
                    "page": 1,
                    "text": "test",
                }
            ],
            "next": "cursor_2",
        }
        mock_response_2 = Mock()
        mock_response_2.json.return_value = {
            "rows": [
                {
                    "id": "2",
                    "meeting": "Council",
                    "date": "2025-01-05",
                    "page": 1,
                    "text": "test",
                }
            ],
            "next": "cursor_3",
        }
        mock_client.get.side_effect = [mock_response_1, mock_response_2]

        # Call with all three parameters
        start_date = date(2024, 12, 1)
        end_date = date(2025, 6, 1)
        stats, next_cursor = _backfill_document_type(
            muni=muni,
            table_name="agendas",
            document_type="agenda",
            start_cursor="resume_cursor",
            max_pages=2,
            date_range=(start_date, end_date),
        )

        # Verify first call has cursor AND date filters
        first_call = mock_client.get.call_args_list[0]
        assert first_call[1]["params"]["_next"] == "resume_cursor"
        assert first_call[1]["params"]["date__gte"] == "2024-12-01"
        assert first_call[1]["params"]["date__lte"] == "2025-06-01"

        # Verify stopped at max_pages and returned next cursor
        assert mock_client.get.call_count == 2
        assert next_cursor == "cursor_3"
