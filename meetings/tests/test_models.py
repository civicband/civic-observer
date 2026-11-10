"""Tests for meetings models."""

from datetime import date, datetime

import pytest

from meetings.models import BackfillProgress, MeetingDocument, MeetingPage
from municipalities.models import Muni


@pytest.mark.django_db
class TestBackfillProgress:
    def test_create_backfill_progress(self):
        """Test creating a BackfillProgress record."""
        muni = Muni.objects.create(
            subdomain="test-city",
            name="Test City",
            state="CA",
        )
        progress = BackfillProgress.objects.create(
            municipality=muni,
            document_type="agenda",
            mode="full",
            status="pending",
        )

        assert progress.municipality == muni
        assert progress.document_type == "agenda"
        assert progress.mode == "full"
        assert progress.status == "pending"
        assert progress.next_cursor is None
        assert progress.force_full_backfill is False
        assert isinstance(progress.started_at, datetime)
        assert isinstance(progress.updated_at, datetime)

    def test_unique_together_constraint(self):
        """Test that municipality + document_type must be unique."""
        muni = Muni.objects.create(
            subdomain="test-city",
            name="Test City",
            state="CA",
        )
        BackfillProgress.objects.create(
            municipality=muni,
            document_type="agenda",
            mode="full",
            status="pending",
        )

        # Trying to create duplicate should raise IntegrityError
        from django.db import IntegrityError

        with pytest.raises(IntegrityError):
            BackfillProgress.objects.create(
                municipality=muni,
                document_type="agenda",
                mode="incremental",
                status="pending",
            )

    def test_different_document_types_allowed(self):
        """Test that same municipality can have different document types."""
        muni = Muni.objects.create(
            subdomain="test-city",
            name="Test City",
            state="CA",
        )

        # Create progress for agendas
        agenda_progress = BackfillProgress.objects.create(
            municipality=muni,
            document_type="agenda",
            mode="full",
            status="pending",
        )

        # Create progress for minutes - should succeed
        minutes_progress = BackfillProgress.objects.create(
            municipality=muni,
            document_type="minutes",
            mode="full",
            status="pending",
        )

        # Verify both exist
        assert BackfillProgress.objects.filter(municipality=muni).count() == 2
        assert agenda_progress.document_type == "agenda"
        assert minutes_progress.document_type == "minutes"


@pytest.mark.django_db
class TestBackfillJobModel:
    @pytest.fixture
    def muni(self):
        return Muni.objects.create(
            subdomain="oakland.ca",
            name="Oakland",
            state="CA",
            country="US",
            kind="city",
        )

    def test_create_backfill_job(self, muni):
        """Test creating a backfill job with default values."""
        from meetings.models import BackfillJob

        job = BackfillJob.objects.create(
            municipality=muni,
            document_type="agenda",
        )

        assert job.municipality == muni
        assert job.document_type == "agenda"
        assert job.status == "pending"
        assert job.last_cursor == ""
        assert job.pages_fetched == 0
        assert job.pages_created == 0
        assert job.pages_updated == 0
        assert job.errors_encountered == 0
        assert job.expected_count is None
        assert job.actual_count is None
        assert job.verified_at is None
        assert job.last_error == ""
        assert job.retry_count == 0
        assert job.created is not None
        assert job.modified is not None

    def test_backfill_job_str_representation(self, muni):
        """Test string representation of BackfillJob."""
        from meetings.models import BackfillJob

        job = BackfillJob.objects.create(
            municipality=muni,
            document_type="minutes",
            status="running",
        )

        expected = "oakland.ca - minutes - running"
        assert str(job) == expected

    def test_backfill_job_status_choices(self, muni):
        """Test all valid status choices."""
        from meetings.models import BackfillJob

        valid_statuses = ["pending", "running", "completed", "failed", "paused"]

        for status in valid_statuses:
            job = BackfillJob.objects.create(
                municipality=muni,
                document_type="agenda",
                status=status,
            )
            job.refresh_from_db()
            assert job.status == status


@pytest.mark.django_db
class TestMeetingPageImageURLs:
    @pytest.fixture
    def muni(self):
        return Muni.objects.create(
            subdomain="alameda.ca",
            name="Alameda",
            state="CA",
            country="US",
            kind="city",
        )

    @pytest.fixture
    def document(self, muni):
        return MeetingDocument.objects.create(
            municipality=muni,
            meeting_name="CityCouncil",
            meeting_date=date(2024, 1, 15),
            document_type="agenda",
        )

    def test_uses_cdn_subdomain_and_width(self, document, settings):
        settings.CDN_URL = "https://cdn.example.com"
        page = MeetingPage.objects.create(
            id="image-page",
            document=document,
            page_number=1,
            page_image="/_agendas/CityCouncil/2024-01-15/1.png",
        )

        assert (
            page.page_image_url
            == "https://cdn.example.com/alameda.ca/_agendas/CityCouncil/2024-01-15/1.png?width=800"
        )
        assert (
            page.page_image_thumbnail_url
            == "https://cdn.example.com/alameda.ca/_agendas/CityCouncil/2024-01-15/1.png?width=400"
        )

    def test_empty_without_image_or_subdomain(self, document):
        page = MeetingPage.objects.create(
            id="no-image", document=document, page_number=1
        )
        assert page.page_image_url == ""
        assert page.page_image_thumbnail_url == ""

        page.page_image = "agendas/CityCouncil/2024-01-15/1.png"
        page.municipality_subdomain = ""
        assert page.page_image_url == ""

    def test_adds_leading_slash(self, document):
        page = MeetingPage.objects.create(
            id="slashless",
            document=document,
            page_number=1,
            page_image="agendas/CityCouncil/2024-01-15/1.png",
        )

        assert page.page_image_url.endswith(
            "/alameda.ca/agendas/CityCouncil/2024-01-15/1.png?width=800"
        )
