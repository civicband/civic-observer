# Resilient Backfill System Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a checkpoint/resume backfill system with verification to ensure all 12M MeetingPage records are captured.

**Architecture:** Add BackfillJob model to track progress per municipality+document_type, ResilientBackfillService with exponential backoff retry and per-batch checkpointing, verification engine to compare API counts vs local counts, and management command interface.

**Tech Stack:** Django 5.2, PostgreSQL 17, httpx for HTTP client, pytest for testing

---

## Task 1: Create BackfillJob Model

**Files:**
- Create: `meetings/models.py` (add BackfillJob class at end)
- Create: `meetings/migrations/0005_backfilljob.py` (auto-generated)
- Test: `meetings/tests.py` (add TestBackfillJobModel class)

### Step 1: Write failing test for BackfillJob model

Add to `meetings/tests.py` after existing test classes:

```python
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

        expected = f"oakland.ca - minutes - running"
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
```

### Step 2: Run test to verify it fails

Run: `uv run pytest meetings/tests.py::TestBackfillJobModel::test_create_backfill_job -v`

Expected: FAIL with "ImportError: cannot import name 'BackfillJob'"

### Step 3: Create BackfillJob model

Add to `meetings/models.py` at the end of the file:

```python
class BackfillJob(TimeStampedModel):
    """
    Tracks progress and state for municipality meeting data backfill operations.

    Provides checkpoint/resume capability so large backfills can recover from
    failures without starting over.
    """

    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("running", "Running"),
        ("completed", "Completed"),
        ("failed", "Failed"),
        ("paused", "Paused"),
    ]

    municipality = models.ForeignKey(
        "municipalities.Muni",
        on_delete=models.CASCADE,
        related_name="backfill_jobs",
    )
    document_type = models.CharField(
        max_length=10,
        choices=MeetingDocument.DOCUMENT_TYPE_CHOICES,
        help_text="Type of document being backfilled (agenda or minutes)",
    )

    # State tracking
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="pending",
        db_index=True,
    )

    # Progress tracking (checkpoint data)
    last_cursor = models.TextField(
        blank=True,
        default="",
        help_text="Pagination cursor to resume from if interrupted",
    )
    pages_fetched = models.IntegerField(
        default=0,
        help_text="Total number of page records fetched from API",
    )
    pages_created = models.IntegerField(
        default=0,
        help_text="Number of new MeetingPage records created",
    )
    pages_updated = models.IntegerField(
        default=0,
        help_text="Number of existing MeetingPage records updated",
    )
    errors_encountered = models.IntegerField(
        default=0,
        help_text="Number of errors encountered during backfill",
    )

    # Verification data
    expected_count = models.IntegerField(
        null=True,
        blank=True,
        help_text="Expected total page count from API metadata",
    )
    actual_count = models.IntegerField(
        null=True,
        blank=True,
        help_text="Actual page count in local database after backfill",
    )
    verified_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp when verification was performed",
    )

    # Error details
    last_error = models.TextField(
        blank=True,
        default="",
        help_text="Last error message encountered",
    )
    retry_count = models.IntegerField(
        default=0,
        help_text="Number of retry attempts for this job",
    )

    class Meta:
        verbose_name = "Backfill Job"
        verbose_name_plural = "Backfill Jobs"
        ordering = ["-created"]
        indexes = [
            models.Index(
                fields=["municipality", "document_type", "status"],
                name="backfill_muni_type_status_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.municipality.subdomain} - {self.document_type} - {self.status}"
```

### Step 4: Create migration

Run: `uv run python manage.py makemigrations meetings`

Expected: Creates `meetings/migrations/0005_backfilljob.py`

### Step 5: Run migration

Run: `docker-compose up -d db && sleep 5 && uv run python manage.py migrate meetings`

Expected: Migration applies successfully

### Step 6: Run tests to verify they pass

Run: `uv run pytest meetings/tests.py::TestBackfillJobModel -v`

Expected: All 3 tests PASS

### Step 7: Commit

```bash
git add meetings/models.py meetings/tests.py meetings/migrations/0005_backfilljob.py
git commit -m "feat(meetings): add BackfillJob model for checkpoint/resume backfill"
```

---

## Task 2: Create ResilientBackfillService Foundation

**Files:**
- Create: `meetings/resilient_backfill.py` (new service module)
- Test: `meetings/test_resilient_backfill.py` (new test file)

### Step 1: Write failing test for service initialization

Create `meetings/test_resilient_backfill.py`:

```python
import pytest
from unittest.mock import Mock, patch

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
```

### Step 2: Run test to verify it fails

Run: `uv run pytest meetings/test_resilient_backfill.py::TestResilientBackfillService::test_service_initialization -v`

Expected: FAIL with "ModuleNotFoundError: No module named 'meetings.resilient_backfill'"

### Step 3: Create ResilientBackfillService class skeleton

Create `meetings/resilient_backfill.py`:

```python
"""
Resilient backfill service with checkpoint/resume capability.

This service provides robust backfilling of meeting data from civic.band
with automatic retry, progress checkpointing, and verification.
"""

import logging
import time
from typing import Any

import httpx
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from meetings.models import BackfillJob, MeetingDocument, MeetingPage
from meetings.services import BackfillError

logger = logging.getLogger(__name__)


class ResilientBackfillService:
    """
    Service for backfilling meeting data with checkpoint/resume capability.

    Features:
    - Automatic retry with exponential backoff
    - Progress checkpointing after each batch
    - Resume from last cursor if interrupted
    - Per-page error handling (don't fail entire document)
    - Verification against API counts
    """

    def __init__(self, job: BackfillJob, batch_size: int = 1000):
        """
        Initialize the resilient backfill service.

        Args:
            job: BackfillJob instance to track progress
            batch_size: Number of records to fetch per API call (default: 1000)
        """
        self.job = job
        self.batch_size = batch_size

        # Create HTTP client with generous timeout
        timeout = httpx.Timeout(
            connect=30.0,  # Connection timeout
            read=120.0,  # Read timeout (large responses)
            write=120.0,  # Write timeout
            pool=120.0,  # Pool timeout
        )

        headers = self._build_headers()
        self.client = httpx.Client(timeout=timeout, headers=headers)

    def _build_headers(self) -> dict[str, str]:
        """Build HTTP headers including service secret if configured."""
        headers = {}
        service_secret = getattr(settings, "CORKBOARD_SERVICE_SECRET", "")
        if service_secret:
            headers["X-Service-Secret"] = service_secret
        return headers

    def close(self) -> None:
        """Close the HTTP client connection."""
        self.client.close()

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - ensures client is closed."""
        self.close()
```

### Step 4: Run tests to verify they pass

Run: `uv run pytest meetings/test_resilient_backfill.py::TestResilientBackfillService -v`

Expected: Both tests PASS

### Step 5: Commit

```bash
git add meetings/resilient_backfill.py meetings/test_resilient_backfill.py
git commit -m "feat(meetings): add ResilientBackfillService skeleton with HTTP client setup"
```

---

## Task 3: Implement Fetch with Retry Logic

**Files:**
- Modify: `meetings/resilient_backfill.py` (add _fetch_with_retry method)
- Test: `meetings/test_resilient_backfill.py` (add retry tests)

### Step 1: Write failing test for retry logic

Add to `meetings/test_resilient_backfill.py` in the TestResilientBackfillService class:

```python
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
        Mock(json=lambda: {"rows": [], "next": None}, raise_for_status=lambda: None),
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
        service._fetch_with_retry("http://test.civic.band/agendas.json", max_retries=3)

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
```

### Step 2: Run tests to verify they fail

Run: `uv run pytest meetings/test_resilient_backfill.py::TestResilientBackfillService::test_fetch_with_retry_succeeds_first_attempt -v`

Expected: FAIL with "AttributeError: 'ResilientBackfillService' object has no attribute '_fetch_with_retry'"

### Step 3: Implement _fetch_with_retry method

Add to `meetings/resilient_backfill.py` in the ResilientBackfillService class:

```python
def _fetch_with_retry(self, url: str, max_retries: int = 3) -> dict[str, Any]:
    """
    Fetch URL with exponential backoff retry on timeout.

    Args:
        url: URL to fetch
        max_retries: Maximum number of retry attempts (default: 3)

    Returns:
        JSON response data as dictionary

    Raises:
        httpx.TimeoutException: If all retries are exhausted
        httpx.HTTPError: For non-timeout HTTP errors (no retry)
    """
    for attempt in range(max_retries):
        try:
            logger.info(f"Fetching {url} (attempt {attempt + 1}/{max_retries})")
            response = self.client.get(url)
            response.raise_for_status()
            return response.json()

        except httpx.TimeoutException as e:
            if attempt == max_retries - 1:
                # Last attempt - re-raise
                logger.error(f"Timeout after {max_retries} attempts: {e}")
                raise

            # Exponential backoff: 2^0=1s, 2^1=2s, 2^2=4s
            wait_time = 2**attempt
            logger.warning(
                f"Timeout on attempt {attempt + 1}, " f"retrying in {wait_time}s: {e}"
            )
            time.sleep(wait_time)

        except httpx.HTTPError as e:
            # HTTP errors (4xx, 5xx) - don't retry
            logger.error(f"HTTP error: {e}")
            raise

    # Should never reach here due to raise in loop
    raise RuntimeError("Unexpected code path in _fetch_with_retry")
```

### Step 4: Run tests to verify they pass

Run: `uv run pytest meetings/test_resilient_backfill.py::TestResilientBackfillService -v`

Expected: All tests PASS (6 tests total)

### Step 5: Commit

```bash
git add meetings/resilient_backfill.py meetings/test_resilient_backfill.py
git commit -m "feat(meetings): add retry logic with exponential backoff to ResilientBackfillService"
```

---

## Task 4: Implement URL Building Methods

**Files:**
- Modify: `meetings/resilient_backfill.py` (add URL building methods)
- Test: `meetings/test_resilient_backfill.py` (add URL building tests)

### Step 1: Write failing tests for URL building

Add to `meetings/test_resilient_backfill.py`:

```python
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
    data = {}

    url = service._get_next_url(data)

    assert url is None
```

### Step 2: Run tests to verify they fail

Run: `uv run pytest meetings/test_resilient_backfill.py::TestResilientBackfillService::test_build_base_url -v`

Expected: FAIL with "AttributeError: 'ResilientBackfillService' object has no attribute '_build_base_url'"

### Step 3: Implement URL building methods

Add to `meetings/resilient_backfill.py`:

```python
def _build_base_url(self) -> str:
    """
    Build base URL for the civic.band API.

    Returns:
        Base URL for the municipality and document type
    """
    muni = self.job.municipality
    table_name = "agendas" if self.job.document_type == "agenda" else "minutes"
    return f"https://{muni.subdomain}.civic.band/meetings/{table_name}.json"


def _build_initial_url(self) -> str:
    """
    Build starting URL, resuming from checkpoint if exists.

    Returns:
        URL to begin fetching (either first page or resume point)
    """
    base_url = self._build_base_url()

    # Resume from last checkpoint if job was interrupted
    if self.job.last_cursor:
        logger.info(f"Resuming from cursor: {self.job.last_cursor[:50]}...")
        return f"{base_url}?_size={self.batch_size}&_next={self.job.last_cursor}"

    # Start from beginning
    return f"{base_url}?_size={self.batch_size}"


def _get_next_url(self, data: dict[str, Any]) -> str | None:
    """
    Get URL for next page of results.

    Args:
        data: API response data containing optional 'next' cursor

    Returns:
        URL for next page, or None if no more pages
    """
    next_cursor = data.get("next")
    if next_cursor:
        base_url = self._build_base_url()
        return f"{base_url}?_size={self.batch_size}&_next={next_cursor}"
    return None
```

### Step 4: Run tests to verify they pass

Run: `uv run pytest meetings/test_resilient_backfill.py::TestResilientBackfillService -v`

Expected: All tests PASS (12 tests total)

### Step 5: Commit

```bash
git add meetings/resilient_backfill.py meetings/test_resilient_backfill.py
git commit -m "feat(meetings): add URL building methods with checkpoint resume support"
```

---

## Task 5: Implement Checkpoint Update Logic

**Files:**
- Modify: `meetings/resilient_backfill.py` (add _update_checkpoint method)
- Test: `meetings/test_resilient_backfill.py` (add checkpoint tests)

### Step 1: Write failing test for checkpoint updates

Add to `meetings/test_resilient_backfill.py`:

```python
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
```

### Step 2: Run tests to verify they fail

Run: `uv run pytest meetings/test_resilient_backfill.py::TestResilientBackfillService::test_update_checkpoint -v`

Expected: FAIL with "AttributeError: 'ResilientBackfillService' object has no attribute '_update_checkpoint'"

### Step 3: Implement _update_checkpoint method

Add to `meetings/resilient_backfill.py`:

```python
def _update_checkpoint(self, cursor: str | None, stats: dict[str, int]) -> None:
    """
    Save checkpoint after processing batch.

    Updates the BackfillJob with current progress so backfill can
    resume from this point if interrupted.

    Args:
        cursor: Pagination cursor for next batch (None if final batch)
        stats: Statistics from this batch (pages_created, pages_updated, errors)
    """
    self.job.last_cursor = cursor or ""
    self.job.pages_fetched += self.batch_size
    self.job.pages_created += stats.get("pages_created", 0)
    self.job.pages_updated += stats.get("pages_updated", 0)
    self.job.errors_encountered += stats.get("errors", 0)

    self.job.save(
        update_fields=[
            "last_cursor",
            "pages_fetched",
            "pages_created",
            "pages_updated",
            "errors_encountered",
            "modified",  # TimeStampedModel auto-updates this
        ]
    )

    logger.info(
        f"Checkpoint saved: {self.job.pages_fetched} fetched, "
        f"{self.job.pages_created} created, {self.job.pages_updated} updated, "
        f"{self.job.errors_encountered} errors"
    )
```

### Step 4: Run tests to verify they pass

Run: `uv run pytest meetings/test_resilient_backfill.py::TestResilientBackfillService -v`

Expected: All tests PASS (15 tests total)

### Step 5: Commit

```bash
git add meetings/resilient_backfill.py meetings/test_resilient_backfill.py
git commit -m "feat(meetings): add checkpoint update logic for progress tracking"
```

---

## Task 6: Implement Batch Processing with Per-Page Error Handling

**Files:**
- Modify: `meetings/resilient_backfill.py` (add _process_batch and helper methods)
- Test: `meetings/test_resilient_backfill.py` (add batch processing tests)

### Step 1: Write failing test for batch processing

Add to `meetings/test_resilient_backfill.py`:

```python
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
    assert stats["errors"] >= 1

    assert MeetingPage.objects.filter(id="page-1").exists()
    assert not MeetingPage.objects.filter(id="page-2").exists()
    assert MeetingPage.objects.filter(id="page-3").exists()
```

### Step 2: Run tests to verify they fail

Run: `uv run pytest meetings/test_resilient_backfill.py::TestResilientBackfillService::test_process_batch_creates_documents_and_pages -v`

Expected: FAIL with "AttributeError: 'ResilientBackfillService' object has no attribute '_process_batch'"

### Step 3: Implement _process_batch and helper methods

Add to `meetings/resilient_backfill.py`:

```python
def _process_batch(self, rows: list[dict[str, Any]]) -> dict[str, int]:
    """
    Process batch of rows with per-page error handling.

    Groups rows by document (meeting + date), then processes each page
    individually so one bad page doesn't fail the entire document.

    Args:
        rows: List of row dictionaries from API

    Returns:
        Statistics dictionary with pages_created, pages_updated, errors
    """
    stats = {"pages_created": 0, "pages_updated": 0, "errors": 0}

    # Group rows by document (meeting, date)
    documents_map = self._group_rows_by_document(rows, stats)

    # Process each document independently
    for doc_key, pages_data in documents_map.items():
        try:
            with transaction.atomic():
                document = self._get_or_create_document(doc_key)

                # Process pages individually (don't fail whole doc if one page fails)
                for page_data in pages_data:
                    try:
                        created = self._create_or_update_page(document, page_data)
                        if created:
                            stats["pages_created"] += 1
                        else:
                            stats["pages_updated"] += 1

                    except Exception as e:
                        # Log error but continue with other pages
                        logger.warning(
                            f"Failed to process page {page_data.get('id')}: {e}",
                            exc_info=True,
                        )
                        stats["errors"] += 1

        except Exception as e:
            # Document creation failed - log and continue
            logger.error(f"Failed to create document {doc_key}: {e}", exc_info=True)
            stats["errors"] += 1

    return stats


def _group_rows_by_document(
    self, rows: list[dict[str, Any]], stats: dict[str, int]
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """
    Group rows by (meeting_name, meeting_date) to create documents.

    Args:
        rows: List of row dictionaries from API
        stats: Statistics dictionary to update with errors

    Returns:
        Dictionary mapping (meeting_name, date_str) to list of page data
    """
    from datetime import date

    documents_map: dict[tuple[str, str], list[dict[str, Any]]] = {}

    for row in rows:
        try:
            meeting_name = row.get("meeting", "")
            date_str = row.get("date", "")

            if not meeting_name or not date_str:
                logger.warning(f"Skipping row with missing data: {row}")
                stats["errors"] += 1
                continue

            # Validate date format
            date.fromisoformat(date_str)

            key = (meeting_name, date_str)
            if key not in documents_map:
                documents_map[key] = []
            documents_map[key].append(row)

        except (ValueError, TypeError) as e:
            logger.warning(f"Error processing row {row}: {e}")
            stats["errors"] += 1

    return documents_map


def _get_or_create_document(self, doc_key: tuple[str, str]) -> MeetingDocument:
    """
    Get or create a MeetingDocument.

    Args:
        doc_key: Tuple of (meeting_name, date_str)

    Returns:
        MeetingDocument instance
    """
    from datetime import date

    meeting_name, date_str = doc_key
    meeting_date = date.fromisoformat(date_str)

    document, created = MeetingDocument.objects.update_or_create(
        municipality=self.job.municipality,
        meeting_name=meeting_name,
        meeting_date=meeting_date,
        document_type=self.job.document_type,
    )

    return document


def _create_or_update_page(
    self, document: MeetingDocument, page_data: dict[str, Any]
) -> bool:
    """
    Create or update a MeetingPage.

    Args:
        document: MeetingDocument this page belongs to
        page_data: Dictionary with page data from API

    Returns:
        True if page was created, False if updated

    Raises:
        ValueError: If page_id is missing
    """
    page_id = page_data.get("id")
    if not page_id:
        raise ValueError(f"Missing page ID in data: {page_data}")

    page_number = page_data.get("page", 0)
    text = page_data.get("text", "")
    page_image = page_data.get("page_image", "")

    page, created = MeetingPage.objects.update_or_create(
        id=page_id,
        defaults={
            "document": document,
            "page_number": page_number,
            "text": text,
            "page_image": page_image,
        },
    )

    return created
```

### Step 4: Run tests to verify they pass

Run: `uv run pytest meetings/test_resilient_backfill.py::TestResilientBackfillService -v`

Expected: All tests PASS (19 tests total)

### Step 5: Commit

```bash
git add meetings/resilient_backfill.py meetings/test_resilient_backfill.py
git commit -m "feat(meetings): add batch processing with per-page error handling"
```

---

## Task 7: Implement Verification Engine

**Files:**
- Modify: `meetings/resilient_backfill.py` (add verification methods)
- Test: `meetings/test_resilient_backfill.py` (add verification tests)

### Step 1: Write failing test for verification

Add to `meetings/test_resilient_backfill.py`:

```python
@patch("httpx.Client.get")
def test_verify_completeness_with_matching_counts(self, mock_get, job):
    """Test verification passes when counts match."""
    from tests.factories import MeetingPageFactory, MeetingDocumentFactory

    # Create 10 pages in database
    doc = MeetingDocumentFactory(
        municipality=job.municipality,
        document_type="agenda",
    )
    for i in range(10):
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
    from tests.factories import MeetingPageFactory, MeetingDocumentFactory

    # Create 5 pages in database
    doc = MeetingDocumentFactory(
        municipality=job.municipality,
        document_type="agenda",
    )
    for i in range(5):
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
    from tests.factories import MeetingPageFactory, MeetingDocumentFactory

    # Create 999 pages in database
    doc = MeetingDocumentFactory(
        municipality=job.municipality,
        document_type="agenda",
    )
    for i in range(999):
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
    from tests.factories import MeetingPageFactory, MeetingDocumentFactory

    # Create 5 agenda pages for this municipality
    agenda_doc = MeetingDocumentFactory(
        municipality=job.municipality,
        document_type="agenda",
    )
    for i in range(5):
        MeetingPageFactory(document=agenda_doc)

    # Create 3 minutes pages (should not be counted)
    minutes_doc = MeetingDocumentFactory(
        municipality=job.municipality,
        document_type="minutes",
    )
    for i in range(3):
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
    for i in range(2):
        MeetingPageFactory(document=other_doc)

    service = ResilientBackfillService(job)
    count = service._get_local_count()

    assert count == 5  # Only agenda pages for job.municipality
```

### Step 2: Run tests to verify they fail

Run: `uv run pytest meetings/test_resilient_backfill.py::TestResilientBackfillService::test_verify_completeness_with_matching_counts -v`

Expected: FAIL with "AttributeError: 'ResilientBackfillService' object has no attribute '_verify_completeness'"

### Step 3: Implement verification methods

Add to `meetings/resilient_backfill.py`:

```python
def _verify_completeness(self) -> None:
    """
    Verify backfill completeness by comparing local vs API counts.

    Raises:
        BackfillError: If >1% of expected data is missing
    """
    logger.info(f"Verifying backfill completeness for job {self.job.id}")

    # Get expected count from API
    expected = self._get_api_total_count()

    # Get actual count from local database
    actual = self._get_local_count()

    # Update job with verification results
    self.job.expected_count = expected
    self.job.actual_count = actual
    self.job.verified_at = timezone.now()
    self.job.save(
        update_fields=["expected_count", "actual_count", "verified_at", "modified"]
    )

    # Check if counts match
    if actual < expected:
        missing = expected - actual
        error_msg = f"Missing {missing} pages! Expected {expected}, got {actual}"
        logger.error(error_msg)

        # Mark as failed if significant data is missing
        missing_pct = (missing / expected) if expected > 0 else 0
        if missing > 100 or missing_pct > 0.01:  # >1% missing
            self.job.status = "failed"
            self.job.last_error = error_msg
            self.job.save(update_fields=["status", "last_error", "modified"])
            raise BackfillError(error_msg)
        else:
            logger.warning(
                f"Minor discrepancy: {missing} pages missing ({missing_pct:.2%})"
            )

    logger.info(f"Verification passed: {actual}/{expected} pages")


def _get_api_total_count(self) -> int:
    """
    Get total record count from API.

    Returns:
        Expected number of pages from API metadata
    """
    base_url = self._build_base_url()

    # Datasette provides count in the response metadata
    # Fetch first page to get total count
    response = self.client.get(f"{base_url}?_size=1")
    response.raise_for_status()
    data = response.json()

    # Check for count in response (datasette format varies)
    if "filtered_table_rows_count" in data:
        return data["filtered_table_rows_count"]
    elif "count" in data:
        return data["count"]
    else:
        # Fallback: count by fetching all pages (expensive but accurate)
        logger.warning("API doesn't provide count metadata, counting all pages")
        return self._count_all_api_pages()


def _get_local_count(self) -> int:
    """
    Get count of pages in local database for this job.

    Returns:
        Number of MeetingPage records matching municipality and document_type
    """
    return MeetingPage.objects.filter(
        document__municipality=self.job.municipality,
        document__document_type=self.job.document_type,
    ).count()


def _count_all_api_pages(self) -> int:
    """
    Fallback: count all pages by iterating through API (slow but accurate).

    Returns:
        Total count of pages by iterating all API responses
    """
    count = 0
    url = f"{self._build_base_url()}?_size={self.batch_size}"

    while url:
        response = self.client.get(url)
        response.raise_for_status()
        data = response.json()
        count += len(data.get("rows", []))

        next_cursor = data.get("next")
        if next_cursor:
            url = (
                f"{self._build_base_url()}?_size={self.batch_size}&_next={next_cursor}"
            )
        else:
            break

    return count
```

### Step 4: Run tests to verify they pass

Run: `uv run pytest meetings/test_resilient_backfill.py::TestResilientBackfillService -v`

Expected: All tests PASS (23 tests total)

### Step 5: Commit

```bash
git add meetings/resilient_backfill.py meetings/test_resilient_backfill.py
git commit -m "feat(meetings): add verification engine to compare API vs local counts"
```

---

## Task 8: Implement Main Run Method

**Files:**
- Modify: `meetings/resilient_backfill.py` (add run method and error handling)
- Test: `meetings/test_resilient_backfill.py` (add integration test)

### Step 1: Write failing integration test

Add to `meetings/test_resilient_backfill.py`:

```python
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
```

### Step 2: Run tests to verify they fail

Run: `uv run pytest meetings/test_resilient_backfill.py::TestResilientBackfillService::test_run_complete_backfill_flow -v`

Expected: FAIL with "AttributeError: 'ResilientBackfillService' object has no attribute 'run'"

### Step 3: Implement run method

Add to `meetings/resilient_backfill.py`:

```python
def run(self) -> dict[str, int]:
    """
    Run the backfill with automatic checkpointing and verification.

    Returns:
        Dictionary with statistics (pages_created, pages_updated, errors)

    Raises:
        BackfillError: If backfill fails or verification fails
    """
    logger.info(f"Starting resilient backfill for job {self.job.id}")

    # Mark job as running
    self.job.status = "running"
    self.job.save(update_fields=["status", "modified"])

    total_stats = {"pages_created": 0, "pages_updated": 0, "errors": 0}

    try:
        # Resume from last checkpoint if exists
        url = self._build_initial_url()

        # Fetch and process batches
        while url:
            # Fetch batch with retry logic
            data = self._fetch_with_retry(url, max_retries=3)

            # Process batch
            batch_stats = self._process_batch(data.get("rows", []))

            # Accumulate stats
            for key in total_stats:
                total_stats[key] += batch_stats[key]

            # Update checkpoint (save progress)
            self._update_checkpoint(cursor=data.get("next"), stats=batch_stats)

            # Get next URL
            url = self._get_next_url(data)

        # Verify completeness after fetching all data
        self._verify_completeness()

        # Mark as completed
        self.job.status = "completed"
        self.job.save(update_fields=["status", "modified"])

        logger.info(
            f"Resilient backfill completed for job {self.job.id}: {total_stats}"
        )
        return total_stats

    except Exception as e:
        # Handle failure
        self._handle_failure(e)
        raise


def _handle_failure(self, error: Exception) -> None:
    """
    Handle backfill failure by updating job status.

    Args:
        error: Exception that caused the failure
    """
    logger.error(f"Backfill failed for job {self.job.id}: {error}", exc_info=True)

    self.job.status = "failed"
    self.job.last_error = str(error)
    self.job.retry_count += 1
    self.job.save(update_fields=["status", "last_error", "retry_count", "modified"])
```

### Step 4: Run tests to verify they pass

Run: `uv run pytest meetings/test_resilient_backfill.py -v`

Expected: All tests PASS (26 tests total)

### Step 5: Commit

```bash
git add meetings/resilient_backfill.py meetings/test_resilient_backfill.py
git commit -m "feat(meetings): add main run method with complete backfill flow"
```

---

## Task 9: Create Management Command

**Files:**
- Create: `meetings/management/commands/resilient_backfill.py`
- Test: `meetings/test_resilient_backfill_command.py`

### Step 1: Write failing test for management command

Create `meetings/test_resilient_backfill_command.py`:

```python
import pytest
from io import StringIO
from unittest.mock import Mock, patch

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
        assert job.status == "completed"

        # Verify service was called
        mock_service.run.assert_called_once()

        # Verify output
        output = out.getvalue()
        assert "oakland.ca" in output
        assert "100 created" in output

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
        job = BackfillJob.objects.create(
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
```

### Step 2: Run tests to verify they fail

Run: `uv run pytest meetings/test_resilient_backfill_command.py::TestResilientBackfillCommand::test_command_creates_job_and_runs_backfill -v`

Expected: FAIL with "ModuleNotFoundError: No module named 'meetings.management'"

### Step 3: Create management command

Create `meetings/management/__init__.py` (empty file)

Create `meetings/management/commands/__init__.py` (empty file)

Create `meetings/management/commands/resilient_backfill.py`:

```python
"""
Management command for resilient backfill of meeting data.

Usage:
    python manage.py resilient_backfill --subdomain=oakland.ca
    python manage.py resilient_backfill --subdomain=all --document-type=both
    python manage.py resilient_backfill --subdomain=all --resume
"""

from django.core.management.base import BaseCommand, CommandError

from meetings.models import BackfillJob
from meetings.resilient_backfill import ResilientBackfillService
from municipalities.models import Muni


class Command(BaseCommand):
    help = "Backfill meeting data with checkpoint/resume capability"

    def add_arguments(self, parser):
        parser.add_argument(
            "--subdomain",
            type=str,
            required=True,
            help='Municipality subdomain to backfill (or "all" for all municipalities)',
        )
        parser.add_argument(
            "--document-type",
            type=str,
            choices=["agenda", "minutes", "both"],
            default="both",
            help="Document type to backfill",
        )
        parser.add_argument(
            "--resume",
            action="store_true",
            help="Resume failed/paused jobs instead of creating new ones",
        )
        parser.add_argument(
            "--verify-only",
            action="store_true",
            help="Only verify existing data without fetching",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=1000,
            help="Number of records to fetch per API call",
        )

    def handle(self, *args, **options):
        subdomain = options["subdomain"]
        doc_type = options["document_type"]
        resume = options["resume"]
        verify_only = options["verify_only"]
        batch_size = options["batch_size"]

        # Get municipalities to process
        if subdomain == "all":
            munis = Muni.objects.all()
        else:
            munis = Muni.objects.filter(subdomain=subdomain)

        if not munis.exists():
            raise CommandError(f"No municipalities found for subdomain: {subdomain}")

        # Process each municipality
        for muni in munis:
            self.stdout.write(f"\nProcessing {muni.subdomain}...")

            # Determine which document types to process
            doc_types = ["agenda", "minutes"] if doc_type == "both" else [doc_type]

            for dt in doc_types:
                if resume:
                    job = self._resume_job(muni, dt)
                else:
                    job = self._create_job(muni, dt)

                if verify_only:
                    self._verify_job(job)
                else:
                    self._run_job(job, batch_size)

    def _create_job(self, muni: Muni, doc_type: str) -> BackfillJob:
        """Create a new backfill job."""
        job = BackfillJob.objects.create(
            municipality=muni,
            document_type=doc_type,
            status="pending",
        )
        self.stdout.write(f"  Created job {job.id} for {doc_type}")
        return job

    def _resume_job(self, muni: Muni, doc_type: str) -> BackfillJob:
        """Resume an existing failed/paused job or create new one."""
        job = (
            BackfillJob.objects.filter(
                municipality=muni,
                document_type=doc_type,
                status__in=["failed", "paused"],
            )
            .order_by("-created")
            .first()
        )

        if job:
            self.stdout.write(
                self.style.WARNING(f"  Resuming job {job.id} from cursor position")
            )
        else:
            job = self._create_job(muni, doc_type)

        return job

    def _run_job(self, job: BackfillJob, batch_size: int) -> None:
        """Run a backfill job."""
        try:
            with ResilientBackfillService(job, batch_size=batch_size) as service:
                service.run()

            self.stdout.write(
                self.style.SUCCESS(
                    f"  ✓ {job.document_type}: "
                    f"{job.pages_created} created, {job.pages_updated} updated, "
                    f"{job.errors_encountered} errors"
                )
            )

            # Show verification results
            if job.expected_count and job.actual_count:
                if job.actual_count == job.expected_count:
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"  ✓ Verified: {job.actual_count}/{job.expected_count} pages"
                        )
                    )
                else:
                    missing = job.expected_count - job.actual_count
                    self.stdout.write(
                        self.style.ERROR(
                            f"  ✗ Missing {missing} pages! "
                            f"({job.actual_count}/{job.expected_count})"
                        )
                    )

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"  ✗ Failed: {e}"))

    def _verify_job(self, job: BackfillJob) -> None:
        """Verify an existing job without fetching."""
        with ResilientBackfillService(job) as service:
            service._verify_completeness()

        if job.actual_count == job.expected_count:
            self.stdout.write(
                self.style.SUCCESS(
                    f"  ✓ {job.document_type}: "
                    f"{job.actual_count}/{job.expected_count} pages"
                )
            )
        else:
            missing = job.expected_count - job.actual_count
            self.stdout.write(
                self.style.ERROR(
                    f"  ✗ {job.document_type}: Missing {missing} pages! "
                    f"({job.actual_count}/{job.expected_count})"
                )
            )
```

### Step 4: Run tests to verify they pass

Run: `uv run pytest meetings/test_resilient_backfill_command.py -v`

Expected: All tests PASS (6 tests total)

### Step 5: Commit

```bash
git add meetings/management/ meetings/test_resilient_backfill_command.py
git commit -m "feat(meetings): add resilient_backfill management command"
```

---

## Task 10: Add BackfillJob to Django Admin

**Files:**
- Modify: `meetings/admin.py` (add BackfillJobAdmin)

### Step 1: Add BackfillJobAdmin to admin.py

Add to `meetings/admin.py`:

```python
from meetings.models import MeetingDocument, MeetingPage, BackfillJob


@admin.register(BackfillJob)
class BackfillJobAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "municipality",
        "document_type",
        "status",
        "pages_created",
        "pages_updated",
        "errors_encountered",
        "created",
        "verified_at",
    ]
    list_filter = ["status", "document_type", "created"]
    search_fields = ["municipality__subdomain", "municipality__name"]
    readonly_fields = [
        "id",
        "created",
        "modified",
        "verified_at",
        "pages_fetched",
        "pages_created",
        "pages_updated",
        "errors_encountered",
        "expected_count",
        "actual_count",
    ]
    fieldsets = [
        (
            "Job Info",
            {
                "fields": [
                    "id",
                    "municipality",
                    "document_type",
                    "status",
                    "created",
                    "modified",
                ]
            },
        ),
        (
            "Progress",
            {
                "fields": [
                    "last_cursor",
                    "pages_fetched",
                    "pages_created",
                    "pages_updated",
                    "errors_encountered",
                ]
            },
        ),
        (
            "Verification",
            {
                "fields": [
                    "expected_count",
                    "actual_count",
                    "verified_at",
                ]
            },
        ),
        (
            "Errors",
            {
                "fields": [
                    "last_error",
                    "retry_count",
                ]
            },
        ),
    ]
```

### Step 2: Verify admin registration works

Run Django server and check admin:
```bash
docker-compose up -d db
uv run python manage.py runserver
```

Visit http://localhost:8000/admin/ and verify BackfillJob appears in admin.

### Step 3: Commit

```bash
git add meetings/admin.py
git commit -m "feat(meetings): add BackfillJob to Django admin"
```

---

## Task 11: Update Documentation

**Files:**
- Create: `docs/RESILIENT_BACKFILL.md` (user guide)
- Modify: `README.md` (add link to guide)

### Step 1: Create user documentation

Create `docs/RESILIENT_BACKFILL.md`:

```markdown
# Resilient Backfill System

The resilient backfill system provides reliable backfilling of meeting data from civic.band with automatic checkpoint/resume capability and verification.

## Features

- **Checkpoint/Resume**: Automatically saves progress every 1000 records. If interrupted, resumes from last checkpoint.
- **Retry Logic**: Automatically retries timeouts with exponential backoff (1s, 2s, 4s).
- **Per-Page Error Handling**: One bad page doesn't fail entire document or batch.
- **Verification**: Compares local counts vs API counts to ensure completeness.
- **Progress Tracking**: View progress in Django admin or database.

## Usage

### Basic Commands

```bash
# Backfill one municipality (agendas + minutes)
python manage.py resilient_backfill --subdomain=oakland.ca

# Backfill all municipalities
python manage.py resilient_backfill --subdomain=all

# Backfill only agendas
python manage.py resilient_backfill --subdomain=oakland.ca --document-type=agenda

# Resume failed jobs
python manage.py resilient_backfill --subdomain=all --resume

# Verify existing data without fetching
python manage.py resilient_backfill --subdomain=all --verify-only
```

### Custom Batch Size

For slower APIs or large municipalities, reduce batch size:

```bash
python manage.py resilient_backfill --subdomain=oakland.ca --batch-size=500
```

## Monitoring Progress

### Django Admin

Visit `/admin/meetings/backfilljob/` to view:
- Job status (pending, running, completed, failed, paused)
- Progress (pages fetched, created, updated)
- Verification results (expected vs actual counts)
- Error messages

### Database Queries

```python
from meetings.models import BackfillJob

# Check failed jobs
failed = BackfillJob.objects.filter(status="failed")
for job in failed:
    print(f"{job.municipality.subdomain}: {job.last_error}")

# Check jobs with missing data
incomplete = BackfillJob.objects.filter(
    status="completed", actual_count__lt=models.F("expected_count")
)
```

## Troubleshooting

### Job Failed with Timeout

Increase timeout or reduce batch size:
```bash
python manage.py resilient_backfill --subdomain=oakland.ca --batch-size=500
```

### Verification Failed (Missing Data)

Re-run backfill for that municipality:
```bash
python manage.py resilient_backfill --subdomain=oakland.ca
```

### Resume Interrupted Job

Jobs automatically checkpoint progress. Simply re-run:
```bash
python manage.py resilient_backfill --subdomain=oakland.ca --resume
```

The job will resume from the last cursor position.

## Architecture

### BackfillJob Model

Tracks progress and state for each municipality+document_type combination:
- `last_cursor`: Pagination cursor to resume from
- `pages_fetched`: Total pages fetched from API
- `pages_created`: New MeetingPage records created
- `pages_updated`: Existing MeetingPage records updated
- `expected_count`: Expected total from API
- `actual_count`: Actual count in local database
- `verified_at`: When verification was performed

### ResilientBackfillService

Core service class with:
- HTTP client with 120s timeout
- Exponential backoff retry (3 attempts)
- Checkpoint after each batch (1000 records)
- Per-page error handling
- Verification engine

## Differences from Original Backfill

The original `backfill_municipality_meetings` function:
- No checkpoint/resume capability
- Transaction rollbacks lose entire documents
- No verification of completeness
- 60s timeout (too short for large responses)

The resilient system adds:
- Checkpoint every 1000 records
- Per-page error handling
- Automatic verification
- 120s timeout with retry

Both systems can coexist. The resilient system is recommended for new backfills.
```

### Step 2: Update README.md

Add to README.md in appropriate section:

```markdown
## Data Backfill

Use the resilient backfill system to populate meeting data:

```bash
python manage.py resilient_backfill --subdomain=all
```

See [Resilient Backfill Guide](docs/RESILIENT_BACKFILL.md) for details.
```

### Step 3: Commit

```bash
git add docs/RESILIENT_BACKFILL.md README.md
git commit -m "docs: add resilient backfill user guide and README updates"
```

---

## Final Verification

### Step 1: Run all tests

Run: `uv run pytest meetings/ -v`

Expected: All tests PASS

### Step 2: Check test coverage

Run: `uv run pytest meetings/ --cov=meetings --cov-report=term-missing`

Expected: >80% coverage for new code

### Step 3: Run linting

Run: `uv run ruff check meetings/`

Expected: No errors

### Step 4: Run type checking

Run: `uv run mypy meetings/`

Expected: No errors

### Step 5: Final commit and summary

```bash
git add -A
git commit -m "chore: final verification and cleanup for resilient backfill"
```

---

## Success Criteria

- ✅ BackfillJob model created and migrated
- ✅ ResilientBackfillService with checkpoint/resume
- ✅ Retry logic with exponential backoff
- ✅ Per-page error handling
- ✅ Verification engine
- ✅ Management command
- ✅ Django admin integration
- ✅ Comprehensive tests (26+ tests)
- ✅ User documentation

**Next Steps:**
1. Test on staging with real civic.band API
2. Run backfill on 1-2 small municipalities
3. Verify data completeness
4. Roll out to all municipalities

**Implementation Plan Complete!**
