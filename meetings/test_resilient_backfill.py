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
