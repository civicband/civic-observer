from unittest.mock import Mock, patch

import httpx
import pytest

from meetings.models import BackfillJob, MeetingDocument, MeetingPage
from meetings.resilient_backfill import ResilientBackfillService
from meetings.services import BackfillError
from municipalities.models import Muni


@pytest.mark.django_db
class TestResilientBackfillService:
    @pytest.fixture
    def muni(self):
        return Muni.objects.create(
            subdomain="berkeley.ca",
            name="Berkeley",
            state="CA",
            country="US",
            kind="city",
        )

    @pytest.fixture
    def job(self, muni):
        return BackfillJob.objects.create(
            municipality=muni,
            document_type="agenda",
        )

    def test_service_initialization(self, job):
        """Test creating a ResilientBackfillService instance."""
        service = ResilientBackfillService(job, batch_size=500)

        assert service.job == job
        assert service.batch_size == 500
        assert service.client is not None
        assert service.client.timeout.connect == 30.0
        assert service.client.timeout.read == 120.0
        assert service.client.timeout.write == 120.0
        assert service.client.timeout.pool == 120.0

    def test_service_uses_default_batch_size(self, job):
        """Test service uses default batch size of 1000."""
        service = ResilientBackfillService(job)

        assert service.batch_size == 1000

    @patch("httpx.Client.get")
    def test_fetch_with_retry_succeeds_first_attempt(self, mock_get, job):
        """Test successful fetch on first attempt."""
        mock_response = Mock()
        mock_response.json.return_value = {"rows": [], "next": None}
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        service = ResilientBackfillService(job)
        result = service._fetch_with_retry("http://test.civic.band/agendas.json")

        assert result == {"rows": [], "next": None}
        assert mock_get.call_count == 1

    @patch("httpx.Client.get")
    @patch("time.sleep")  # Mock sleep to speed up test
    def test_fetch_with_retry_succeeds_after_timeout(self, mock_sleep, mock_get, job):
        """Test successful fetch after timeout retry."""
        # First two calls timeout, third succeeds
        mock_get.side_effect = [
            httpx.TimeoutException("Timeout 1"),
            httpx.TimeoutException("Timeout 2"),
            Mock(
                json=lambda: {"rows": [], "next": None}, raise_for_status=lambda: None
            ),
        ]

        service = ResilientBackfillService(job)
        result = service._fetch_with_retry(
            "http://test.civic.band/agendas.json", max_retries=3
        )

        assert result == {"rows": [], "next": None}
        assert mock_get.call_count == 3
        # Verify exponential backoff: 2^0=1s, 2^1=2s
        assert mock_sleep.call_count == 2
        mock_sleep.assert_any_call(1)
        mock_sleep.assert_any_call(2)

    @patch("httpx.Client.get")
    @patch("time.sleep")
    def test_fetch_with_retry_fails_after_max_retries(self, mock_sleep, mock_get, job):
        """Test fetch fails after exhausting retries."""
        mock_get.side_effect = httpx.TimeoutException("Timeout")

        service = ResilientBackfillService(job)

        with pytest.raises(httpx.TimeoutException):
            service._fetch_with_retry(
                "http://test.civic.band/agendas.json", max_retries=3
            )

        assert mock_get.call_count == 3

    @patch("httpx.Client.get")
    def test_fetch_with_retry_raises_http_error_immediately(self, mock_get, job):
        """Test HTTP errors are raised immediately without retry."""
        mock_get.side_effect = httpx.HTTPStatusError(
            "404 Not Found",
            request=Mock(),
            response=Mock(status_code=404),
        )

        service = ResilientBackfillService(job)

        with pytest.raises(httpx.HTTPStatusError):
            service._fetch_with_retry("http://test.civic.band/agendas.json")

        # Should not retry on HTTP errors
        assert mock_get.call_count == 1

    def test_build_base_url(self, job):
        """Test building base URL for API."""
        service = ResilientBackfillService(job)
        url = service._build_base_url()

        assert url == "https://berkeley.ca.civic.band/meetings/agendas.json"

    def test_build_base_url_for_minutes(self, muni):
        """Test building base URL for minutes."""
        job = BackfillJob.objects.create(
            municipality=muni,
            document_type="minutes",
        )
        service = ResilientBackfillService(job)
        url = service._build_base_url()

        assert url == "https://berkeley.ca.civic.band/meetings/minutes.json"

    def test_build_initial_url_without_cursor(self, job):
        """Test building initial URL without existing cursor."""
        service = ResilientBackfillService(job)
        url = service._build_initial_url()

        expected = "https://berkeley.ca.civic.band/meetings/agendas.json?_size=1000"
        assert url == expected

    def test_build_initial_url_with_cursor(self, job):
        """Test building URL resumes from existing cursor."""
        job.last_cursor = "eyJwYWdlIjogMn0="  # Example cursor
        job.save()

        service = ResilientBackfillService(job)
        url = service._build_initial_url()

        expected = (
            "https://berkeley.ca.civic.band/meetings/agendas.json"
            "?_size=1000&_next=eyJwYWdlIjogMn0="
        )
        assert url == expected

    def test_get_next_url_with_cursor(self, job):
        """Test building next page URL with cursor."""
        service = ResilientBackfillService(job)
        data = {"next": "eyJwYWdlIjogM30="}

        url = service._get_next_url(data)

        expected = (
            "https://berkeley.ca.civic.band/meetings/agendas.json"
            "?_size=1000&_next=eyJwYWdlIjogM30="
        )
        assert url == expected

    def test_get_next_url_without_cursor(self, job):
        """Test get_next_url returns None when no cursor."""
        service = ResilientBackfillService(job)
        data: dict[str, str] = {}

        url = service._get_next_url(data)

        assert url is None

    def test_update_checkpoint(self, job):
        """Test updating checkpoint saves progress to database."""
        service = ResilientBackfillService(job)

        stats = {
            "pages_created": 150,
            "pages_updated": 50,
            "errors": 2,
        }

        service._update_checkpoint(cursor="cursor123", stats=stats)

        # Refresh from database
        job.refresh_from_db()

        assert job.last_cursor == "cursor123"
        assert job.pages_fetched == 1000  # batch_size
        assert job.pages_created == 150
        assert job.pages_updated == 50
        assert job.errors_encountered == 2

    def test_update_checkpoint_accumulates_stats(self, job):
        """Test checkpoint updates accumulate stats across batches."""
        # Set initial values
        job.pages_fetched = 1000
        job.pages_created = 100
        job.pages_updated = 20
        job.errors_encountered = 1
        job.save()

        service = ResilientBackfillService(job)

        stats = {
            "pages_created": 150,
            "pages_updated": 30,
            "errors": 2,
        }

        service._update_checkpoint(cursor="cursor456", stats=stats)

        job.refresh_from_db()

        assert job.last_cursor == "cursor456"
        assert job.pages_fetched == 2000  # 1000 + 1000
        assert job.pages_created == 250  # 100 + 150
        assert job.pages_updated == 50  # 20 + 30
        assert job.errors_encountered == 3  # 1 + 2

    def test_update_checkpoint_with_none_cursor(self, job):
        """Test checkpoint update handles None cursor (final batch)."""
        service = ResilientBackfillService(job)

        stats = {"pages_created": 50, "pages_updated": 10, "errors": 0}

        service._update_checkpoint(cursor=None, stats=stats)

        job.refresh_from_db()

        assert job.last_cursor == ""  # None becomes empty string
        assert job.pages_created == 50

    def test_process_batch_creates_documents_and_pages(self, job):
        """Test processing batch creates MeetingDocuments and MeetingPages."""
        service = ResilientBackfillService(job)

        rows = [
            {
                "id": "page-1",
                "meeting": "CityCouncil",
                "date": "2024-01-15",
                "page": 1,
                "text": "Test page 1",
                "page_image": "/_agendas/CityCouncil/2024-01-15/1.png",
            },
            {
                "id": "page-2",
                "meeting": "CityCouncil",
                "date": "2024-01-15",
                "page": 2,
                "text": "Test page 2",
                "page_image": "/_agendas/CityCouncil/2024-01-15/2.png",
            },
        ]

        stats = service._process_batch(rows)

        assert stats["pages_created"] == 2
        assert stats["pages_updated"] == 0
        assert stats["errors"] == 0

        # Verify document was created
        assert MeetingDocument.objects.filter(
            municipality=job.municipality,
            meeting_name="CityCouncil",
            meeting_date="2024-01-15",
            document_type="agenda",
        ).exists()

        # Verify pages were created
        assert MeetingPage.objects.filter(id="page-1").exists()
        assert MeetingPage.objects.filter(id="page-2").exists()

    def test_process_batch_updates_existing_pages(self, job):
        """Test processing batch updates existing pages."""
        # Create existing document and page
        from tests.factories import MeetingDocumentFactory, MeetingPageFactory

        doc = MeetingDocumentFactory(
            municipality=job.municipality,
            meeting_name="CityCouncil",
            meeting_date="2024-01-15",
            document_type="agenda",
        )
        MeetingPageFactory(
            id="page-1",
            document=doc,
            page_number=1,
            text="Old text",
        )

        service = ResilientBackfillService(job)

        rows = [
            {
                "id": "page-1",
                "meeting": "CityCouncil",
                "date": "2024-01-15",
                "page": 1,
                "text": "Updated text",
                "page_image": "/_agendas/CityCouncil/2024-01-15/1.png",
            },
        ]

        stats = service._process_batch(rows)

        assert stats["pages_created"] == 0
        assert stats["pages_updated"] == 1
        assert stats["errors"] == 0

        # Verify page was updated
        page = MeetingPage.objects.get(id="page-1")
        assert page.text == "Updated text"

    def test_process_batch_handles_missing_required_fields(self, job):
        """Test batch processing skips rows with missing required fields."""
        service = ResilientBackfillService(job)

        rows = [
            {"id": "page-1", "meeting": "", "date": "2024-01-15"},  # Missing meeting
            {"id": "page-2", "meeting": "CityCouncil", "date": ""},  # Missing date
            {"meeting": "CityCouncil", "date": "2024-01-15"},  # Missing id
        ]

        stats = service._process_batch(rows)

        assert stats["pages_created"] == 0
        assert stats["errors"] == 3

    def test_process_batch_continues_after_page_error(self, job):
        """Test batch processing continues even if individual page fails."""
        service = ResilientBackfillService(job)

        rows = [
            {
                "id": "page-1",
                "meeting": "CityCouncil",
                "date": "2024-01-15",
                "page": 1,
                "text": "Good page",
                "page_image": "/_agendas/CityCouncil/2024-01-15/1.png",
            },
            {
                "id": "page-2",
                "meeting": "CityCouncil",
                "date": "invalid-date",  # This will fail
                "page": 2,
                "text": "Bad page",
                "page_image": "/_agendas/CityCouncil/2024-01-15/2.png",
            },
            {
                "id": "page-3",
                "meeting": "CityCouncil",
                "date": "2024-01-15",
                "page": 3,
                "text": "Another good page",
                "page_image": "/_agendas/CityCouncil/2024-01-15/3.png",
            },
        ]

        stats = service._process_batch(rows)

        # Should create 2 pages despite 1 error
        assert stats["pages_created"] == 2
        assert stats["errors"] == 1

        assert MeetingPage.objects.filter(id="page-1").exists()
        assert not MeetingPage.objects.filter(id="page-2").exists()
        assert MeetingPage.objects.filter(id="page-3").exists()

    @patch("httpx.Client.get")
    def test_verify_completeness_with_matching_counts(self, mock_get, job):
        """Test verification passes when counts match."""
        from tests.factories import MeetingDocumentFactory, MeetingPageFactory

        # Create 10 pages in database
        doc = MeetingDocumentFactory(
            municipality=job.municipality,
            document_type="agenda",
        )
        for _ in range(10):
            MeetingPageFactory(document=doc)

        # Mock API to return count of 10
        mock_response = Mock()
        mock_response.json.return_value = {"count": 10, "rows": []}
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        service = ResilientBackfillService(job)
        service._verify_completeness()  # Should not raise

        job.refresh_from_db()
        assert job.expected_count == 10
        assert job.actual_count == 10
        assert job.verified_at is not None

    @patch("httpx.Client.get")
    def test_verify_completeness_fails_with_missing_data(self, mock_get, job):
        """Test verification fails when significant data is missing."""
        from tests.factories import MeetingDocumentFactory, MeetingPageFactory

        # Create 5 pages in database
        doc = MeetingDocumentFactory(
            municipality=job.municipality,
            document_type="agenda",
        )
        for _ in range(5):
            MeetingPageFactory(document=doc)

        # Mock API to return count of 100 (missing 95 pages = 95%)
        mock_response = Mock()
        mock_response.json.return_value = {"count": 100, "rows": []}
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        service = ResilientBackfillService(job)

        with pytest.raises(BackfillError, match="Missing 95 pages"):
            service._verify_completeness()

        job.refresh_from_db()
        assert job.expected_count == 100
        assert job.actual_count == 5
        assert job.status == "failed"

    @patch("httpx.Client.get")
    def test_verify_completeness_allows_minor_discrepancy(self, mock_get, job):
        """Test verification passes with minor discrepancy (<1%)."""
        from tests.factories import MeetingDocumentFactory, MeetingPageFactory

        # Create 999 pages in database
        doc = MeetingDocumentFactory(
            municipality=job.municipality,
            document_type="agenda",
        )
        for _ in range(999):
            MeetingPageFactory(document=doc)

        # Mock API to return count of 1000 (missing 1 page = 0.1%)
        mock_response = Mock()
        mock_response.json.return_value = {"count": 1000, "rows": []}
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        service = ResilientBackfillService(job)
        service._verify_completeness()  # Should not raise (< 1% missing)

        job.refresh_from_db()
        assert job.expected_count == 1000
        assert job.actual_count == 999
        assert job.status != "failed"  # Should not mark as failed

    def test_get_local_count(self, job):
        """Test counting local pages for municipality and document type."""
        from tests.factories import MeetingDocumentFactory, MeetingPageFactory

        # Create 5 agenda pages for this municipality
        agenda_doc = MeetingDocumentFactory(
            municipality=job.municipality,
            document_type="agenda",
        )
        for _ in range(5):
            MeetingPageFactory(document=agenda_doc)

        # Create 3 minutes pages (should not be counted)
        minutes_doc = MeetingDocumentFactory(
            municipality=job.municipality,
            document_type="minutes",
        )
        for _ in range(3):
            MeetingPageFactory(document=minutes_doc)

        # Create 2 agenda pages for different municipality (should not be counted)
        other_muni = Muni.objects.create(
            subdomain="other.ca",
            name="Other City",
            state="CA",
            country="US",
            kind="city",
        )
        other_doc = MeetingDocumentFactory(
            municipality=other_muni,
            document_type="agenda",
        )
        for _ in range(2):
            MeetingPageFactory(document=other_doc)

        service = ResilientBackfillService(job)
        count = service._get_local_count()

        assert count == 5  # Only agenda pages for job.municipality

    @patch("httpx.Client.get")
    def test_run_complete_backfill_flow(self, mock_get, job):
        """Test complete backfill flow from start to verification."""
        # Mock API responses
        mock_responses = [
            # First batch
            Mock(
                json=lambda: {
                    "rows": [
                        {
                            "id": "page-1",
                            "meeting": "CityCouncil",
                            "date": "2024-01-15",
                            "page": 1,
                            "text": "Page 1",
                            "page_image": "/_agendas/CityCouncil/2024-01-15/1.png",
                        },
                        {
                            "id": "page-2",
                            "meeting": "CityCouncil",
                            "date": "2024-01-15",
                            "page": 2,
                            "text": "Page 2",
                            "page_image": "/_agendas/CityCouncil/2024-01-15/2.png",
                        },
                    ],
                    "next": "cursor123",
                },
                raise_for_status=lambda: None,
            ),
            # Second batch (final)
            Mock(
                json=lambda: {
                    "rows": [
                        {
                            "id": "page-3",
                            "meeting": "CityCouncil",
                            "date": "2024-01-15",
                            "page": 3,
                            "text": "Page 3",
                            "page_image": "/_agendas/CityCouncil/2024-01-15/3.png",
                        },
                    ],
                    "next": None,  # No more pages
                },
                raise_for_status=lambda: None,
            ),
            # Verification count request
            Mock(
                json=lambda: {"count": 3, "rows": []},
                raise_for_status=lambda: None,
            ),
        ]
        mock_get.side_effect = mock_responses

        service = ResilientBackfillService(job, batch_size=1000)
        result = service.run()

        # Check result stats
        assert result["pages_created"] == 3
        assert result["pages_updated"] == 0
        assert result["errors"] == 0

        # Check job was updated
        job.refresh_from_db()
        assert job.status == "completed"
        assert job.pages_created == 3
        assert job.pages_fetched == 2000  # 2 batches
        assert job.expected_count == 3
        assert job.actual_count == 3
        assert job.verified_at is not None

        # Verify pages were created
        assert MeetingPage.objects.count() == 3

    @patch("httpx.Client.get")
    def test_run_handles_failure(self, mock_get, job):
        """Test run method handles failures gracefully."""
        mock_get.side_effect = httpx.HTTPStatusError(
            "500 Server Error",
            request=Mock(),
            response=Mock(status_code=500),
        )

        service = ResilientBackfillService(job)

        with pytest.raises(httpx.HTTPStatusError):
            service.run()

        # Job should be marked as failed
        job.refresh_from_db()
        assert job.status == "failed"
        assert "500 Server Error" in job.last_error

    @patch("httpx.Client.get")
    def test_run_resumes_from_checkpoint(self, mock_get, job):
        """Test run method resumes from existing checkpoint."""
        # Set job to have existing checkpoint
        job.last_cursor = "cursor123"
        job.pages_fetched = 1000
        job.pages_created = 2
        job.save()

        # Mock response for resumed batch
        mock_get.return_value = Mock(
            json=lambda: {
                "rows": [
                    {
                        "id": "page-3",
                        "meeting": "CityCouncil",
                        "date": "2024-01-15",
                        "page": 3,
                        "text": "Page 3",
                        "page_image": "/_agendas/CityCouncil/2024-01-15/3.png",
                    },
                ],
                "next": None,
            },
            raise_for_status=lambda: None,
        )

        service = ResilientBackfillService(job)
        result = service.run()

        # Should only create 1 new page (resumed from checkpoint)
        assert result["pages_created"] == 1

        job.refresh_from_db()
        assert job.pages_created == 3  # 2 from before + 1 new
