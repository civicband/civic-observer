"""Tests for meetings background tasks."""

from datetime import date, timedelta
from unittest.mock import patch

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
