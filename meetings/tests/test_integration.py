"""Integration tests for backfill system."""

from datetime import date
from unittest.mock import Mock, patch

import pytest

from meetings.models import BackfillProgress, MeetingDocument
from meetings.tasks import backfill_municipality_meetings_task
from municipalities.models import Muni


@pytest.mark.django_db
class TestBackfillIntegration:
    @patch("meetings.services.httpx.Client")
    @patch("meetings.tasks.django_rq.get_queue")
    def test_end_to_end_new_municipality_full_backfill(
        self, mock_get_queue, mock_client_class
    ):
        """
        Integration test: New municipality → Full backfill mode → Job chaining.
        """
        # Setup
        muni = Muni.objects.create(
            subdomain="test-city",
            name="Test City",
            state="CA",
        )

        # Mock HTTP client for API calls
        mock_client = Mock()
        mock_client_class.return_value.__enter__.return_value = mock_client

        # Mock API response with pagination
        mock_response_1 = Mock()
        mock_response_1.json.return_value = {
            "rows": [
                {
                    "id": "page1",
                    "meeting": "Council",
                    "date": "2024-12-01",
                    "page": 1,
                    "text": "test",
                    "page_image": "",
                }
            ],
            "next": "cursor_2",
        }
        mock_response_2 = Mock()
        mock_response_2.json.return_value = {
            "rows": [
                {
                    "id": "page2",
                    "meeting": "Council",
                    "date": "2024-12-01",
                    "page": 2,
                    "text": "test2",
                    "page_image": "",
                }
            ],
            "next": None,
        }
        mock_client.get.side_effect = [
            mock_response_1,
            mock_response_2,
            mock_response_1,  # For minutes
            mock_response_2,
        ]

        # Mock queue
        mock_queue = Mock()
        mock_get_queue.return_value = mock_queue

        # Act: Run orchestrator
        result = backfill_municipality_meetings_task(muni.id)

        # Assert: Verify full backfill was triggered
        assert "full_backfill_started" in result["agenda"]
        assert "full_backfill_started" in result["minutes"]

        # Verify progress records created
        agenda_progress = BackfillProgress.objects.get(
            municipality=muni, document_type="agenda"
        )
        assert agenda_progress.mode == "full"
        assert agenda_progress.status == "in_progress"

        minutes_progress = BackfillProgress.objects.get(
            municipality=muni, document_type="minutes"
        )
        assert minutes_progress.mode == "full"

        # Verify batch tasks were enqueued (2 total - agenda + minutes)
        assert mock_queue.enqueue.call_count == 2

    @patch("meetings.services.httpx.Client")
    @patch("meetings.tasks.django_rq.get_queue")
    def test_end_to_end_existing_municipality_incremental(
        self, mock_get_queue, mock_client_class
    ):
        """
        Integration test: Existing municipality → Incremental mode → Date filters.
        """
        # Setup: Create municipality with existing documents
        muni = Muni.objects.create(
            subdomain="test-city",
            name="Test City",
            state="CA",
        )
        MeetingDocument.objects.create(
            municipality=muni,
            meeting_name="Council",
            meeting_date=date(2024, 1, 1),
            document_type="agenda",
        )

        # Mock HTTP client
        mock_client = Mock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        mock_response = Mock()
        mock_response.json.return_value = {"rows": [], "next": None}
        mock_client.get.return_value = mock_response

        # Mock queue
        mock_queue = Mock()
        mock_get_queue.return_value = mock_queue

        # Act: Run orchestrator
        result = backfill_municipality_meetings_task(muni.id)

        # Assert: Verify incremental backfill was triggered
        assert "incremental_backfill_started" in result["agenda"]

        # Verify progress record uses incremental mode
        progress = BackfillProgress.objects.get(
            municipality=muni, document_type="agenda"
        )
        assert progress.mode == "incremental"

        # Verify incremental tasks were enqueued
        assert mock_queue.enqueue.call_count == 2  # agenda + minutes
