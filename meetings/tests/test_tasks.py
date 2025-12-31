"""Tests for meetings background tasks."""

from datetime import date, timedelta
from unittest.mock import Mock, patch

import pytest
from django.conf import settings

from meetings.models import BackfillProgress
from meetings.tasks import backfill_incremental_task
from municipalities.models import Muni


@pytest.mark.django_db
class TestBackfillIncrementalTask:
    @patch("meetings.services._backfill_document_type")
    def test_incremental_backfill_uses_date_range(self, mock_backfill):
        """Test that incremental backfill passes ±6 months date range."""
        muni = Muni.objects.create(
            subdomain="test-city",
            name="Test City",
            state="CA",
        )
        progress = BackfillProgress.objects.create(
            municipality=muni,
            document_type="agenda",
            mode="incremental",
            status="in_progress",
        )

        # Mock the backfill function
        mock_backfill.return_value = (
            {"documents_created": 5, "pages_created": 50},
            None,  # No next cursor for incremental
        )

        # Run the task
        backfill_incremental_task(muni.id, "agenda", progress.id)

        # Verify date range was calculated correctly (±6 months)
        call_args = mock_backfill.call_args
        date_range = call_args[1]["date_range"]
        start_date, end_date = date_range

        today = date.today()
        months = getattr(settings, "INCREMENTAL_BACKFILL_MONTHS", 6)
        expected_start = today - timedelta(days=months * 30)
        expected_end = today + timedelta(days=months * 30)

        assert start_date == expected_start
        assert end_date == expected_end

        # Verify progress was marked complete
        progress.refresh_from_db()
        assert progress.status == "completed"

    @patch("meetings.services._backfill_document_type")
    def test_incremental_backfill_handles_errors(self, mock_backfill):
        """Test that errors are caught and progress marked as failed."""
        muni = Muni.objects.create(
            subdomain="test-city",
            name="Test City",
            state="CA",
        )
        progress = BackfillProgress.objects.create(
            municipality=muni,
            document_type="agenda",
            mode="incremental",
            status="in_progress",
        )

        # Mock the backfill function to raise an error
        mock_backfill.side_effect = Exception("API Error")

        # Run the task - should raise but update progress
        with pytest.raises(Exception, match="API Error"):
            backfill_incremental_task(muni.id, "agenda", progress.id)

        # Verify progress was marked failed with error message
        progress.refresh_from_db()
        assert progress.status == "failed"
        assert progress.error_message is not None
        assert "API Error" in progress.error_message


@pytest.mark.django_db
class TestBackfillBatchTask:
    @patch("meetings.services._backfill_document_type")
    @patch("meetings.tasks.django_rq.get_queue")
    def test_batch_task_chains_when_more_pages(self, mock_get_queue, mock_backfill):
        """Test that batch task enqueues next batch when cursor exists."""
        muni = Muni.objects.create(
            subdomain="test-city",
            name="Test City",
            state="CA",
        )
        progress = BackfillProgress.objects.create(
            municipality=muni,
            document_type="agenda",
            mode="full",
            status="in_progress",
            next_cursor=None,
        )

        # Mock backfill to return stats and a next cursor
        mock_backfill.return_value = (
            {"documents_created": 10, "pages_created": 100},
            "next_cursor_abc123",
        )

        # Mock the queue
        mock_queue = Mock()
        mock_get_queue.return_value = mock_queue

        # Run the task
        from meetings.tasks import backfill_batch_task

        backfill_batch_task(muni.id, "agenda", progress.id)

        # Verify backfill was called with max_pages=10
        call_args = mock_backfill.call_args
        assert call_args[1]["max_pages"] == 10
        assert call_args[1]["start_cursor"] is None

        # Verify progress was updated with cursor
        progress.refresh_from_db()
        assert progress.next_cursor == "next_cursor_abc123"
        assert progress.status == "in_progress"

        # Verify next batch was enqueued
        mock_queue.enqueue.assert_called_once()
        enqueue_args = mock_queue.enqueue.call_args[0]
        assert enqueue_args[0].__name__ == "backfill_batch_task"
        assert enqueue_args[1] == muni.id
        assert enqueue_args[2] == "agenda"
        assert enqueue_args[3] == progress.id

    @patch("meetings.services._backfill_document_type")
    @patch("meetings.tasks.django_rq.get_queue")
    def test_batch_task_completes_when_no_more_pages(
        self, mock_get_queue, mock_backfill
    ):
        """Test that batch task marks complete when no next cursor."""
        muni = Muni.objects.create(
            subdomain="test-city",
            name="Test City",
            state="CA",
        )
        progress = BackfillProgress.objects.create(
            municipality=muni,
            document_type="agenda",
            mode="full",
            status="in_progress",
            force_full_backfill=True,  # Test that flag gets cleared
        )

        # Mock backfill to return stats with NO next cursor
        mock_backfill.return_value = (
            {"documents_created": 5, "pages_created": 50},
            None,  # No more pages
        )

        mock_queue = Mock()
        mock_get_queue.return_value = mock_queue

        # Run the task
        from meetings.tasks import backfill_batch_task

        backfill_batch_task(muni.id, "agenda", progress.id)

        # Verify progress was marked completed
        progress.refresh_from_db()
        assert progress.status == "completed"
        assert progress.next_cursor is None
        assert progress.force_full_backfill is False  # Flag cleared

        # Verify NO next batch was enqueued
        mock_queue.enqueue.assert_not_called()

    @patch("meetings.services._backfill_document_type")
    def test_batch_task_resumes_from_cursor(self, mock_backfill):
        """Test that batch task resumes from saved cursor."""
        muni = Muni.objects.create(
            subdomain="test-city",
            name="Test City",
            state="CA",
        )
        progress = BackfillProgress.objects.create(
            municipality=muni,
            document_type="agenda",
            mode="full",
            status="in_progress",
            next_cursor="resume_from_here",
        )

        # Mock backfill
        mock_backfill.return_value = ({"documents_created": 5}, None)

        # Run the task
        from meetings.tasks import backfill_batch_task

        backfill_batch_task(muni.id, "agenda", progress.id)

        # Verify backfill was called with the saved cursor
        call_args = mock_backfill.call_args
        assert call_args[1]["start_cursor"] == "resume_from_here"

    @patch("meetings.services._backfill_document_type")
    def test_batch_task_handles_errors(self, mock_backfill):
        """Test that errors are caught and progress marked as failed."""
        muni = Muni.objects.create(
            subdomain="test-city",
            name="Test City",
            state="CA",
        )
        progress = BackfillProgress.objects.create(
            municipality=muni,
            document_type="agenda",
            mode="full",
            status="in_progress",
        )

        # Mock the backfill function to raise an error
        mock_backfill.side_effect = Exception("API Error")

        # Run the task - should raise but update progress
        with pytest.raises(Exception, match="API Error"):
            from meetings.tasks import backfill_batch_task

            backfill_batch_task(muni.id, "agenda", progress.id)

        # Verify progress was marked failed with error message
        progress.refresh_from_db()
        assert progress.status == "failed"
        assert progress.error_message is not None
        assert "API Error" in progress.error_message

    @patch("meetings.services._backfill_document_type")
    def test_batch_task_resets_status_on_retry(self, mock_backfill):
        """Test that retrying a failed batch resets status to in_progress."""
        muni = Muni.objects.create(
            subdomain="test-city",
            name="Test City",
            state="CA",
        )
        # Setup failed progress from a previous attempt
        progress = BackfillProgress.objects.create(
            municipality=muni,
            document_type="agenda",
            mode="full",
            status="failed",  # Previous failure
            error_message="Previous error",
        )

        # Mock successful backfill this time
        mock_backfill.return_value = (
            {"documents_created": 5, "pages_created": 50},
            None,
        )

        # Retry the task
        from meetings.tasks import backfill_batch_task

        backfill_batch_task(muni.id, "agenda", progress.id)

        # Verify status was reset and task completed successfully
        progress.refresh_from_db()
        assert progress.status == "completed"
        assert progress.error_message is None
