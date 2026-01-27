from io import StringIO
from unittest.mock import Mock, patch

import pytest
from django.core.management import call_command

from meetings.models import BackfillJob
from municipalities.models import Muni


@pytest.mark.django_db
class TestResilientBackfillCommand:
    @pytest.fixture
    def muni(self):
        return Muni.objects.create(
            subdomain="oakland.ca",
            name="Oakland",
            state="CA",
            country="US",
            kind="city",
        )

    @patch("meetings.management.commands.resilient_backfill.ResilientBackfillService")
    def test_command_creates_job_and_runs_backfill(self, mock_service_class, muni):
        """Test command creates BackfillJob and runs backfill."""
        mock_service = Mock()
        mock_service.run.return_value = {
            "pages_created": 100,
            "pages_updated": 10,
            "errors": 0,
        }
        mock_service_class.return_value.__enter__.return_value = mock_service

        out = StringIO()
        call_command(
            "resilient_backfill",
            "--subdomain=oakland.ca",
            "--document-type=agenda",
            stdout=out,
        )

        # Verify job was created
        job = BackfillJob.objects.get(
            municipality=muni,
            document_type="agenda",
        )
        assert job.municipality == muni
        assert job.document_type == "agenda"

        # Verify service was called with the job
        mock_service_class.assert_called_once()
        call_args = mock_service_class.call_args
        assert call_args[0][0] == job  # First arg is the job
        assert call_args[1]["batch_size"] == 1000  # batch_size kwarg

        # Verify service.run() was called
        mock_service.run.assert_called_once()

        # Verify output
        output = out.getvalue()
        assert "oakland.ca" in output

    @patch("meetings.management.commands.resilient_backfill.ResilientBackfillService")
    def test_command_processes_all_municipalities(self, mock_service_class, muni):
        """Test command with --subdomain=all processes all municipalities."""
        # Create second municipality
        Muni.objects.create(
            subdomain="berkeley.ca",
            name="Berkeley",
            state="CA",
            country="US",
            kind="city",
        )

        mock_service = Mock()
        mock_service.run.return_value = {
            "pages_created": 10,
            "pages_updated": 0,
            "errors": 0,
        }
        mock_service_class.return_value.__enter__.return_value = mock_service

        call_command(
            "resilient_backfill",
            "--subdomain=all",
            "--document-type=agenda",
            stdout=StringIO(),
        )

        # Should create 2 jobs (one per municipality)
        assert BackfillJob.objects.count() == 2

    @patch("meetings.management.commands.resilient_backfill.ResilientBackfillService")
    def test_command_processes_both_document_types(self, mock_service_class, muni):
        """Test command with --document-type=both processes agendas and minutes."""
        mock_service = Mock()
        mock_service.run.return_value = {
            "pages_created": 10,
            "pages_updated": 0,
            "errors": 0,
        }
        mock_service_class.return_value.__enter__.return_value = mock_service

        call_command(
            "resilient_backfill",
            "--subdomain=oakland.ca",
            "--document-type=both",
            stdout=StringIO(),
        )

        # Should create 2 jobs (agenda + minutes)
        assert BackfillJob.objects.filter(municipality=muni).count() == 2
        assert BackfillJob.objects.filter(
            municipality=muni, document_type="agenda"
        ).exists()
        assert BackfillJob.objects.filter(
            municipality=muni, document_type="minutes"
        ).exists()

    @patch("meetings.management.commands.resilient_backfill.ResilientBackfillService")
    def test_command_resume_option(self, mock_service_class, muni):
        """Test command --resume option resumes existing failed job."""
        # Create existing failed job
        failed_job = BackfillJob.objects.create(
            municipality=muni,
            document_type="agenda",
            status="failed",
            last_cursor="cursor123",
        )

        mock_service = Mock()
        mock_service.run.return_value = {
            "pages_created": 10,
            "pages_updated": 0,
            "errors": 0,
        }
        mock_service_class.return_value.__enter__.return_value = mock_service

        call_command(
            "resilient_backfill",
            "--subdomain=oakland.ca",
            "--document-type=agenda",
            "--resume",
            stdout=StringIO(),
        )

        # Should reuse existing job, not create new one
        assert BackfillJob.objects.count() == 1

        # Verify same job was used
        mock_service_class.assert_called_once()
        call_args = mock_service_class.call_args[0]
        assert call_args[0].id == failed_job.id

    @patch("meetings.management.commands.resilient_backfill.ResilientBackfillService")
    def test_command_verify_only_option(self, mock_service_class, muni):
        """Test command --verify-only option only verifies without fetching."""
        BackfillJob.objects.create(
            municipality=muni,
            document_type="agenda",
            status="completed",
        )

        mock_service = Mock()
        mock_service_class.return_value.__enter__.return_value = mock_service

        call_command(
            "resilient_backfill",
            "--subdomain=oakland.ca",
            "--document-type=agenda",
            "--verify-only",
            stdout=StringIO(),
        )

        # Should call verify but not run
        mock_service._verify_completeness.assert_called_once()
        mock_service.run.assert_not_called()

    @patch("meetings.management.commands.resilient_backfill.ResilientBackfillService")
    def test_command_batch_size_option(self, mock_service_class, muni):
        """Test command --batch-size option is passed to service."""
        mock_service = Mock()
        mock_service.run.return_value = {
            "pages_created": 10,
            "pages_updated": 0,
            "errors": 0,
        }
        mock_service_class.return_value.__enter__.return_value = mock_service

        call_command(
            "resilient_backfill",
            "--subdomain=oakland.ca",
            "--document-type=agenda",
            "--batch-size=500",
            stdout=StringIO(),
        )

        # Verify service was called with batch_size=500
        mock_service_class.assert_called_once()
        call_kwargs = mock_service_class.call_args[1]
        assert call_kwargs["batch_size"] == 500
