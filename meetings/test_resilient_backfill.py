from unittest.mock import Mock, patch

import httpx
import pytest

from meetings.models import BackfillJob
from meetings.resilient_backfill import ResilientBackfillService
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
