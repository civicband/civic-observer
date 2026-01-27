# Resilient Backfill System Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement two-mode backfill system (full + incremental) with job chaining and progress checkpointing to handle municipalities with 547k+ pages without RQ timeouts.

**Architecture:** Create BackfillProgress model for state tracking, modify existing backfill service to support date filtering and batching, implement new task functions for incremental (±6 months) and batched full backfill with job chaining.

**Tech Stack:** Django 5.2, django-rq 3.1, PostgreSQL 17, httpx, pytest-django

---

## Phase 1: Create BackfillProgress Model

### Task 1.1: Write BackfillProgress model test

**Files:**
- Create: `meetings/tests/test_models.py`

**Step 1: Write the failing test**

```python
"""Tests for meetings models."""

import pytest
from datetime import datetime
from django.utils import timezone
from municipalities.models import Muni
from meetings.models import BackfillProgress


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
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest meetings/tests/test_models.py::TestBackfillProgress -v`

Expected: FAIL with "ModuleNotFoundError: No module named 'meetings.tests'"

**Step 3: Create test file structure**

```bash
mkdir -p meetings/tests
touch meetings/tests/__init__.py
```

**Step 4: Run test again to verify model doesn't exist**

Run: `uv run pytest meetings/tests/test_models.py::TestBackfillProgress::test_create_backfill_progress -v`

Expected: FAIL with "ImportError: cannot import name 'BackfillProgress'"

**Step 5: Create BackfillProgress model**

Modify: `meetings/models.py` (add after MeetingPage model)

```python
class BackfillProgress(models.Model):
    """
    Tracks progress of backfill operations for municipalities.

    Stores checkpoints for resumable backfills and configuration flags
    for controlling backfill mode (full vs incremental).
    """

    DOCUMENT_TYPE_CHOICES = [
        ("agenda", "Agenda"),
        ("minutes", "Minutes"),
    ]

    MODE_CHOICES = [
        ("full", "Full"),
        ("incremental", "Incremental"),
    ]

    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("in_progress", "In Progress"),
        ("completed", "Completed"),
        ("failed", "Failed"),
    ]

    municipality = models.ForeignKey(
        "municipalities.Muni",
        on_delete=models.CASCADE,
        related_name="backfill_progress",
    )
    document_type = models.CharField(
        max_length=20,
        choices=DOCUMENT_TYPE_CHOICES,
    )
    mode = models.CharField(
        max_length=20,
        choices=MODE_CHOICES,
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="pending",
    )
    next_cursor = models.TextField(
        blank=True,
        null=True,
        help_text="Pagination cursor for resuming backfill",
    )
    force_full_backfill = models.BooleanField(
        default=False,
        help_text="Set to True to force full backfill on next run",
    )
    started_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    error_message = models.TextField(
        blank=True,
        null=True,
        help_text="Error message if backfill failed",
    )

    class Meta:
        unique_together = [["municipality", "document_type"]]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["municipality", "status"]),
        ]
        verbose_name = "Backfill Progress"
        verbose_name_plural = "Backfill Progress"

    def __str__(self):
        return f"{self.municipality.subdomain} - {self.document_type} ({self.status})"
```

**Step 6: Run test to verify it passes**

Run: `uv run pytest meetings/tests/test_models.py::TestBackfillProgress -v`

Expected: PASS (2 tests)

**Step 7: Commit**

```bash
git add meetings/models.py meetings/tests/__init__.py meetings/tests/test_models.py
git commit -m "feat(meetings): add BackfillProgress model for resumable backfills"
```

---

### Task 1.2: Create database migration

**Files:**
- Create: `meetings/migrations/000X_add_backfillprogress.py` (auto-generated)

**Step 1: Generate migration**

Run: `uv run python manage.py makemigrations meetings`

Expected: Creates migration file with BackfillProgress model

**Step 2: Review migration**

Run: `cat meetings/migrations/000X_*.py | head -50`

Expected: Should see CreateModel operation for BackfillProgress

**Step 3: Run migration**

Run: `DATABASE_URL="postgres://postgres@localhost:5433/postgres" uv run python manage.py migrate`

Expected: "Applying meetings.000X_add_backfillprogress... OK"

**Step 4: Verify tests still pass with real DB**

Run: `uv run pytest meetings/tests/test_models.py::TestBackfillProgress -v`

Expected: PASS (2 tests)

**Step 5: Commit migration**

```bash
git add meetings/migrations/000X_*.py
git commit -m "feat(meetings): add migration for BackfillProgress model"
```

---

## Phase 2: Add Configuration Constants

### Task 2.1: Add backfill configuration to settings

**Files:**
- Modify: `config/settings/base.py` (after RQ_QUEUES configuration)

**Step 1: Add configuration constants**

```python
# Backfill Configuration
INCREMENTAL_BACKFILL_MONTHS = 6  # ±6 months from today
FULL_BACKFILL_BATCH_SIZE = 10  # API pages per job (10 pages = ~10k records)
BACKFILL_API_PAGE_SIZE = 1000  # Records per API request
```

**Step 2: Verify settings load**

Run: `uv run python manage.py shell -c "from django.conf import settings; print(settings.INCREMENTAL_BACKFILL_MONTHS)"`

Expected: Outputs "6"

**Step 3: Commit**

```bash
git add config/settings/base.py
git commit -m "feat(settings): add backfill configuration constants"
```

---

## Phase 3: Update Service for Date Filtering and Batching

### Task 3.1: Test date filtering in backfill service

**Files:**
- Create: `meetings/tests/test_services.py`

**Step 1: Write failing test for date filtering**

```python
"""Tests for meetings services."""

import pytest
from datetime import date, timedelta
from unittest.mock import Mock, patch, call
from municipalities.models import Muni
from meetings.services import _backfill_document_type


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
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest meetings/tests/test_services.py::TestBackfillDocumentType::test_backfill_with_date_range_filter -v`

Expected: FAIL - _backfill_document_type doesn't accept date_range parameter

**Step 3: Update _backfill_document_type signature and implementation**

Modify: `meetings/services.py` - Update the `_backfill_document_type` function

```python
def _backfill_document_type(
    muni: Muni,
    table_name: str,
    document_type: str,
    start_cursor: str | None = None,
    max_pages: int | None = None,
    date_range: tuple[date, date] | None = None,
    timeout: int = 60,
) -> tuple[dict[str, int], str | None]:
    """
    Backfill a specific document type (agendas or minutes) for a municipality.

    Args:
        muni: Municipality instance
        table_name: Name of the datasette table ('agendas' or 'minutes')
        document_type: Type of document ('agenda' or 'minutes')
        start_cursor: Pagination cursor to resume from (optional)
        max_pages: Maximum number of API pages to fetch (optional, for batching)
        date_range: Tuple of (start_date, end_date) for incremental backfill (optional)
        timeout: HTTP request timeout in seconds

    Returns:
        Tuple of (stats dict, next_cursor or None)
    """
    stats = {
        "documents_created": 0,
        "documents_updated": 0,
        "pages_created": 0,
        "pages_updated": 0,
        "errors": 0,
    }

    base_url = f"https://{muni.subdomain}.civic.band/meetings/{table_name}.json"

    try:
        # Build headers with service secret for authentication
        headers = {}
        service_secret = getattr(settings, "CORKBOARD_SERVICE_SECRET", "")
        if service_secret:
            headers["X-Service-Secret"] = service_secret

        with httpx.Client(timeout=timeout, headers=headers) as client:
            # Build query parameters
            params = {"_size": 1000}

            # Add date filtering for incremental mode
            if date_range:
                start_date, end_date = date_range
                params["date__gte"] = start_date.isoformat()
                params["date__lte"] = end_date.isoformat()

            # Resume from cursor if provided
            if start_cursor:
                params["_next"] = start_cursor

            pages_fetched = 0
            next_cursor = None

            while True:
                logger.debug(f"Fetching {base_url} with params {params}")
                response = client.get(base_url, params=params)
                response.raise_for_status()

                data = response.json()

                # Get rows from the response
                rows = data.get("rows", [])

                # Process rows in batches
                _process_rows_batch(muni, rows, document_type, stats)

                pages_fetched += 1

                # Check if there's a next page using the cursor
                next_cursor = data.get("next")

                # Stop if: no more pages OR reached batch limit
                if not next_cursor or (max_pages and pages_fetched >= max_pages):
                    break

                # Use the next cursor for pagination
                params = {"_size": 1000, "_next": next_cursor}

                # Preserve date filters if they were set
                if date_range:
                    params["date__gte"] = start_date.isoformat()
                    params["date__lte"] = end_date.isoformat()

    except httpx.HTTPError as e:
        logger.error(
            f"HTTP error fetching {table_name} for {muni.subdomain}: {e}",
            exc_info=True,
        )
        stats["errors"] += 1

    return stats, next_cursor
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest meetings/tests/test_services.py::TestBackfillDocumentType -v`

Expected: PASS (2 tests)

**Step 5: Commit**

```bash
git add meetings/services.py meetings/tests/test_services.py
git commit -m "feat(meetings): add date filtering and batching to backfill service"
```

---

## Phase 4: Implement Incremental Backfill Task

### Task 4.1: Test incremental backfill task

**Files:**
- Modify: `meetings/tests/test_tasks.py` (if exists) or Create new

**Step 1: Write failing test for incremental backfill**

```python
"""Tests for meetings background tasks."""

import pytest
from datetime import date, timedelta
from unittest.mock import Mock, patch
from django.utils import timezone
from municipalities.models import Muni
from meetings.models import BackfillProgress
from meetings.tasks import backfill_incremental_task


@pytest.mark.django_db
class TestBackfillIncrementalTask:

    @patch("meetings.tasks._backfill_document_type")
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
        stats = backfill_incremental_task(muni.id, "agenda", progress.id)

        # Verify date range was calculated correctly (±6 months)
        call_args = mock_backfill.call_args
        date_range = call_args[1]["date_range"]
        start_date, end_date = date_range

        today = date.today()
        expected_start = today - timedelta(days=180)
        expected_end = today + timedelta(days=180)

        assert start_date == expected_start
        assert end_date == expected_end

        # Verify progress was marked complete
        progress.refresh_from_db()
        assert progress.status == "completed"

    @patch("meetings.tasks._backfill_document_type")
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
        assert "API Error" in progress.error_message
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest meetings/tests/test_tasks.py::TestBackfillIncrementalTask -v`

Expected: FAIL - backfill_incremental_task doesn't exist

**Step 3: Implement backfill_incremental_task**

Modify: `meetings/tasks.py` - Add new function

```python
def backfill_incremental_task(
    muni_id: UUID | str, document_type: str, progress_id: int
) -> dict[str, int]:
    """
    Background task for incremental backfill (±6 months).

    Fetches only meetings within INCREMENTAL_BACKFILL_MONTHS of today
    using date range filters. Designed for daily webhook updates.

    Args:
        muni_id: Municipality primary key
        document_type: 'agenda' or 'minutes'
        progress_id: BackfillProgress record ID

    Returns:
        Statistics dictionary from backfill operation

    Raises:
        Exception: If backfill fails (after updating progress status)
    """
    from django.conf import settings
    from datetime import date, timedelta
    from meetings.models import BackfillProgress
    from meetings.services import _backfill_document_type

    logger.info(
        f"Starting incremental backfill task for municipality ID: {muni_id}, "
        f"document_type: {document_type}"
    )

    try:
        muni = Muni.objects.get(pk=muni_id)
        progress = BackfillProgress.objects.get(pk=progress_id)

        # Calculate date range (±6 months from today)
        today = date.today()
        months = getattr(settings, "INCREMENTAL_BACKFILL_MONTHS", 6)
        start_date = today - timedelta(days=months * 30)
        end_date = today + timedelta(days=months * 30)

        logger.info(
            f"Incremental backfill for {muni.subdomain} {document_type}: "
            f"{start_date} to {end_date}"
        )

        # Fetch with date filters
        table_name = "agendas" if document_type == "agenda" else "minutes"
        stats, _ = _backfill_document_type(
            muni=muni,
            table_name=table_name,
            document_type=document_type,
            date_range=(start_date, end_date),
        )

        # Mark progress as completed
        progress.status = "completed"
        progress.error_message = None
        progress.save()

        logger.info(
            f"Incremental backfill completed for {muni.subdomain} {document_type}: {stats}"
        )

        return stats

    except Muni.DoesNotExist:
        logger.error(f"Municipality with ID {muni_id} does not exist")
        raise
    except Exception as e:
        # Save failure state
        try:
            progress = BackfillProgress.objects.get(pk=progress_id)
            progress.status = "failed"
            progress.error_message = str(e)
            progress.save()
        except Exception as save_error:
            logger.error(f"Failed to update progress status: {save_error}")

        logger.error(
            f"Incremental backfill failed for municipality ID {muni_id}: {e}",
            exc_info=True,
        )
        raise
```

**Step 4: Add import at top of tasks.py**

```python
from meetings.models import BackfillProgress  # Add to imports
```

**Step 5: Run tests to verify they pass**

Run: `uv run pytest meetings/tests/test_tasks.py::TestBackfillIncrementalTask -v`

Expected: PASS (2 tests)

**Step 6: Commit**

```bash
git add meetings/tasks.py meetings/tests/test_tasks.py
git commit -m "feat(meetings): add incremental backfill task with date filtering"
```

---

## Phase 5: Implement Batched Full Backfill Task

### Task 5.1: Test batched backfill task

**Files:**
- Modify: `meetings/tests/test_tasks.py`

**Step 1: Write failing test for batched backfill**

```python
@pytest.mark.django_db
class TestBackfillBatchTask:

    @patch("meetings.tasks._backfill_document_type")
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

        stats = backfill_batch_task(muni.id, "agenda", progress.id)

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

    @patch("meetings.tasks._backfill_document_type")
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

        stats = backfill_batch_task(muni.id, "agenda", progress.id)

        # Verify progress was marked completed
        progress.refresh_from_db()
        assert progress.status == "completed"
        assert progress.next_cursor is None
        assert progress.force_full_backfill is False  # Flag cleared

        # Verify NO next batch was enqueued
        mock_queue.enqueue.assert_not_called()

    @patch("meetings.tasks._backfill_document_type")
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
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest meetings/tests/test_tasks.py::TestBackfillBatchTask -v`

Expected: FAIL - backfill_batch_task doesn't exist

**Step 3: Implement backfill_batch_task**

Modify: `meetings/tasks.py` - Add new function

```python
def backfill_batch_task(
    muni_id: UUID | str, document_type: str, progress_id: int
) -> dict[str, int]:
    """
    Background task for batched full backfill.

    Processes one batch (FULL_BACKFILL_BATCH_SIZE API pages), saves
    checkpoint, and enqueues next batch if more work remains.

    Args:
        muni_id: Municipality primary key
        document_type: 'agenda' or 'minutes'
        progress_id: BackfillProgress record ID

    Returns:
        Statistics dictionary from this batch

    Raises:
        Exception: If batch fails (after updating progress status)
    """
    from django.conf import settings
    from meetings.models import BackfillProgress
    from meetings.services import _backfill_document_type
    import django_rq

    logger.info(
        f"Starting batch backfill task for municipality ID: {muni_id}, "
        f"document_type: {document_type}, progress_id: {progress_id}"
    )

    try:
        muni = Muni.objects.get(pk=muni_id)
        progress = BackfillProgress.objects.get(pk=progress_id)

        # Get batch size from settings
        batch_size = getattr(settings, "FULL_BACKFILL_BATCH_SIZE", 10)

        logger.info(
            f"Processing batch for {muni.subdomain} {document_type}, "
            f"starting from cursor: {progress.next_cursor}"
        )

        # Fetch and process one batch
        table_name = "agendas" if document_type == "agenda" else "minutes"
        stats, next_cursor = _backfill_document_type(
            muni=muni,
            table_name=table_name,
            document_type=document_type,
            start_cursor=progress.next_cursor,
            max_pages=batch_size,
        )

        # Update checkpoint
        progress.next_cursor = next_cursor
        progress.error_message = None

        if next_cursor:
            # More work to do - save and enqueue next batch
            progress.save()

            queue = django_rq.get_queue("default")
            job = queue.enqueue(
                backfill_batch_task,
                muni_id,
                document_type,
                progress_id,
            )
            logger.info(
                f"Enqueued next batch for {muni.subdomain} {document_type} "
                f"(job ID: {job.id})"
            )
        else:
            # Done - mark complete and clear flag
            progress.status = "completed"
            if progress.force_full_backfill:
                progress.force_full_backfill = False
            progress.save()

            logger.info(
                f"Batch backfill completed for {muni.subdomain} {document_type}"
            )

        return stats

    except Muni.DoesNotExist:
        logger.error(f"Municipality with ID {muni_id} does not exist")
        raise
    except Exception as e:
        # Save failure state
        try:
            progress = BackfillProgress.objects.get(pk=progress_id)
            progress.status = "failed"
            progress.error_message = str(e)
            progress.save()
        except Exception as save_error:
            logger.error(f"Failed to update progress status: {save_error}")

        logger.error(
            f"Batch backfill failed for municipality ID {muni_id}: {e}",
            exc_info=True,
        )
        raise
```

**Step 4: Add django_rq import at top of tasks.py**

```python
import django_rq  # Add to imports
```

**Step 5: Run tests to verify they pass**

Run: `uv run pytest meetings/tests/test_tasks.py::TestBackfillBatchTask -v`

Expected: PASS (3 tests)

**Step 6: Commit**

```bash
git add meetings/tasks.py meetings/tests/test_tasks.py
git commit -m "feat(meetings): add batched backfill task with job chaining"
```

---

## Phase 6: Update Orchestrator Task

### Task 6.1: Test orchestrator routing logic

**Files:**
- Modify: `meetings/tests/test_tasks.py`

**Step 1: Write failing test for orchestrator**

```python
@pytest.mark.django_db
class TestBackfillMunicipalityMeetingsTask:

    @patch("meetings.tasks.django_rq.get_queue")
    def test_new_municipality_triggers_full_backfill(self, mock_get_queue):
        """Test that new municipalities use full backfill mode."""
        muni = Muni.objects.create(
            subdomain="test-city",
            name="Test City",
            state="CA",
        )
        # No existing MeetingDocuments - this is a new municipality

        mock_queue = Mock()
        mock_get_queue.return_value = mock_queue

        # Run orchestrator
        from meetings.tasks import backfill_municipality_meetings_task

        backfill_municipality_meetings_task(muni.id)

        # Verify BackfillProgress was created for both agendas and minutes
        agenda_progress = BackfillProgress.objects.get(
            municipality=muni,
            document_type="agenda",
        )
        assert agenda_progress.mode == "full"
        assert agenda_progress.status == "in_progress"

        minutes_progress = BackfillProgress.objects.get(
            municipality=muni,
            document_type="minutes",
        )
        assert minutes_progress.mode == "full"
        assert minutes_progress.status == "in_progress"

        # Verify batched tasks were enqueued (2 calls - agenda + minutes)
        assert mock_queue.enqueue.call_count == 2

        # First call should be backfill_batch_task for agendas
        first_call = mock_queue.enqueue.call_args_list[0][0]
        assert first_call[0].__name__ == "backfill_batch_task"
        assert first_call[2] == "agenda"

    @patch("meetings.tasks.django_rq.get_queue")
    def test_existing_municipality_triggers_incremental(self, mock_get_queue):
        """Test that existing municipalities use incremental mode."""
        from meetings.models import MeetingDocument

        muni = Muni.objects.create(
            subdomain="test-city",
            name="Test City",
            state="CA",
        )
        # Create existing documents to simulate existing municipality
        MeetingDocument.objects.create(
            municipality=muni,
            meeting_name="Council Meeting",
            meeting_date=date(2024, 12, 1),
            document_type="agenda",
        )

        mock_queue = Mock()
        mock_get_queue.return_value = mock_queue

        # Run orchestrator
        from meetings.tasks import backfill_municipality_meetings_task

        backfill_municipality_meetings_task(muni.id)

        # Verify BackfillProgress uses incremental mode
        agenda_progress = BackfillProgress.objects.get(
            municipality=muni,
            document_type="agenda",
        )
        assert agenda_progress.mode == "incremental"
        assert agenda_progress.status == "in_progress"

        # Verify incremental task was enqueued
        first_call = mock_queue.enqueue.call_args_list[0][0]
        assert first_call[0].__name__ == "backfill_incremental_task"

    @patch("meetings.tasks.django_rq.get_queue")
    def test_force_full_backfill_flag_triggers_full(self, mock_get_queue):
        """Test that force_full_backfill flag overrides to full mode."""
        from meetings.models import MeetingDocument

        muni = Muni.objects.create(
            subdomain="test-city",
            name="Test City",
            state="CA",
        )
        # Existing documents
        MeetingDocument.objects.create(
            municipality=muni,
            meeting_name="Council Meeting",
            meeting_date=date(2024, 12, 1),
            document_type="agenda",
        )

        # Create progress with force flag set
        BackfillProgress.objects.create(
            municipality=muni,
            document_type="agenda",
            mode="incremental",
            status="completed",
            force_full_backfill=True,  # Force full backfill
        )

        mock_queue = Mock()
        mock_get_queue.return_value = mock_queue

        # Run orchestrator
        from meetings.tasks import backfill_municipality_meetings_task

        backfill_municipality_meetings_task(muni.id)

        # Verify it switched to full mode
        agenda_progress = BackfillProgress.objects.get(
            municipality=muni,
            document_type="agenda",
        )
        assert agenda_progress.mode == "full"

        # Verify batch task was enqueued (not incremental)
        first_call = mock_queue.enqueue.call_args_list[0][0]
        assert first_call[0].__name__ == "backfill_batch_task"
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest meetings/tests/test_tasks.py::TestBackfillMunicipalityMeetingsTask -v`

Expected: FAIL - orchestrator logic not updated

**Step 3: Update backfill_municipality_meetings_task**

Modify: `meetings/tasks.py` - Replace existing `backfill_municipality_meetings_task` function

```python
def backfill_municipality_meetings_task(muni_id: UUID | str) -> dict[str, str]:
    """
    Main orchestrator task that routes to full or incremental backfill.

    Determines backfill mode based on:
    - New municipality (no existing documents) → Full backfill
    - force_full_backfill flag set → Full backfill
    - Existing municipality → Incremental backfill (±6 months)

    Args:
        muni_id: Primary key of the Municipality to backfill

    Returns:
        Dictionary with status for agendas and minutes

    Raises:
        Muni.DoesNotExist: If the municipality doesn't exist
    """
    from meetings.models import BackfillProgress, MeetingDocument
    import django_rq

    logger.info(f"Starting backfill orchestrator for municipality ID: {muni_id}")

    try:
        muni = Muni.objects.get(pk=muni_id)
        queue = django_rq.get_queue("default")
        result = {}

        # Process both agendas and minutes
        for document_type in ["agenda", "minutes"]:
            # Get or create progress tracker
            progress, created = BackfillProgress.objects.get_or_create(
                municipality=muni,
                document_type=document_type,
            )

            # Determine if full backfill is needed
            is_new = not MeetingDocument.objects.filter(
                municipality=muni,
                document_type=document_type,
            ).exists()
            needs_full = is_new or progress.force_full_backfill

            if needs_full:
                # Start full backfill chain
                progress.mode = "full"
                progress.status = "in_progress"
                progress.next_cursor = None  # Start from beginning
                progress.error_message = None
                progress.save()

                job = queue.enqueue(
                    backfill_batch_task,
                    muni_id,
                    document_type,
                    progress.id,
                )

                reason = "new municipality" if is_new else "force_full_backfill flag"
                logger.info(
                    f"Enqueued full backfill for {muni.subdomain} {document_type} "
                    f"({reason}, job ID: {job.id})"
                )
                result[document_type] = f"full_backfill_started:{job.id}"
            else:
                # Run incremental backfill
                progress.mode = "incremental"
                progress.status = "in_progress"
                progress.error_message = None
                progress.save()

                job = queue.enqueue(
                    backfill_incremental_task,
                    muni_id,
                    document_type,
                    progress.id,
                )

                logger.info(
                    f"Enqueued incremental backfill for {muni.subdomain} {document_type} "
                    f"(job ID: {job.id})"
                )
                result[document_type] = f"incremental_backfill_started:{job.id}"

        # NOTE: We no longer enqueue check_all_immediate_searches here
        # That will be done in the completion handlers of batch/incremental tasks

        return result

    except Muni.DoesNotExist:
        logger.error(f"Municipality with ID {muni_id} does not exist")
        raise
    except Exception as e:
        logger.error(
            f"Backfill orchestrator failed for municipality ID {muni_id}: {e}",
            exc_info=True,
        )
        raise
```

**Step 4: Update imports in tasks.py**

```python
from meetings.models import BackfillProgress, MeetingDocument  # Update imports
```

**Step 5: Run tests to verify they pass**

Run: `uv run pytest meetings/tests/test_tasks.py::TestBackfillMunicipalityMeetingsTask -v`

Expected: PASS (3 tests)

**Step 6: Commit**

```bash
git add meetings/tasks.py meetings/tests/test_tasks.py
git commit -m "feat(meetings): update orchestrator with smart mode routing"
```

---

## Phase 7: Add Admin Interface

### Task 7.1: Register BackfillProgress in admin

**Files:**
- Modify: `meetings/admin.py`

**Step 1: Add BackfillProgress to admin**

```python
from meetings.models import BackfillProgress  # Add to imports


@admin.register(BackfillProgress)
class BackfillProgressAdmin(admin.ModelAdmin):
    """Admin interface for BackfillProgress model."""

    list_display = [
        "municipality_name",
        "document_type",
        "mode",
        "status_badge",
        "updated_at",
        "has_error",
    ]
    list_filter = ["status", "mode", "document_type"]
    search_fields = ["municipality__name", "municipality__subdomain"]
    readonly_fields = [
        "started_at",
        "updated_at",
        "error_message_display",
    ]
    fieldsets = [
        (
            "Backfill Information",
            {
                "fields": [
                    "municipality",
                    "document_type",
                    "mode",
                    "status",
                ]
            },
        ),
        (
            "Progress Tracking",
            {
                "fields": [
                    "next_cursor",
                    "force_full_backfill",
                    "started_at",
                    "updated_at",
                ]
            },
        ),
        (
            "Error Information",
            {
                "fields": ["error_message_display"],
                "classes": ["collapse"],
            },
        ),
    ]
    actions = ["force_full_backfill_action", "retry_failed_action"]

    @admin.display(description="Municipality")
    def municipality_name(self, obj):
        return f"{obj.municipality.name} ({obj.municipality.subdomain})"

    @admin.display(description="Status", ordering="status")
    def status_badge(self, obj):
        colors = {
            "pending": "gray",
            "in_progress": "blue",
            "completed": "green",
            "failed": "red",
        }
        color = colors.get(obj.status, "gray")
        return format_html(
            '<span style="color: {}; font-weight: bold;">{}</span>',
            color,
            obj.get_status_display(),
        )

    @admin.display(description="Has Error", boolean=True)
    def has_error(self, obj):
        return bool(obj.error_message)

    @admin.display(description="Error Message")
    def error_message_display(self, obj):
        if obj.error_message:
            return format_html(
                '<pre style="white-space: pre-wrap;">{}</pre>',
                obj.error_message,
            )
        return "No errors"

    @admin.action(description="Force full backfill on next run")
    def force_full_backfill_action(self, request, queryset):
        """Set force_full_backfill flag and trigger backfill."""
        import django_rq
        from meetings.tasks import backfill_municipality_meetings_task

        queue = django_rq.get_queue("default")
        count = 0

        for progress in queryset:
            progress.force_full_backfill = True
            progress.save()

            # Trigger backfill
            queue.enqueue(
                backfill_municipality_meetings_task,
                progress.municipality.id,
            )
            count += 1

        self.message_user(
            request,
            f"Triggered full backfill for {count} progress record(s).",
        )

    @admin.action(description="Retry failed backfill")
    def retry_failed_action(self, request, queryset):
        """Reset failed backfills to in_progress and re-enqueue."""
        import django_rq
        from meetings.tasks import backfill_batch_task, backfill_incremental_task

        queue = django_rq.get_queue("default")
        count = 0

        for progress in queryset.filter(status="failed"):
            progress.status = "in_progress"
            progress.error_message = None
            progress.save()

            # Enqueue appropriate task based on mode
            if progress.mode == "full":
                task = backfill_batch_task
            else:
                task = backfill_incremental_task

            queue.enqueue(
                task,
                progress.municipality.id,
                progress.document_type,
                progress.id,
            )
            count += 1

        self.message_user(
            request,
            f"Retried {count} failed backfill(s).",
        )
```

**Step 2: Add format_html import**

```python
from django.utils.html import format_html  # Add to imports
```

**Step 3: Verify admin loads without errors**

Run: `uv run python manage.py check`

Expected: "System check identified no issues"

**Step 4: Test admin in Django shell**

Run:
```bash
uv run python manage.py shell -c "
from django.contrib import admin
from meetings.models import BackfillProgress
print('BackfillProgress registered:', BackfillProgress in admin.site._registry)
"
```

Expected: "BackfillProgress registered: True"

**Step 5: Commit**

```bash
git add meetings/admin.py
git commit -m "feat(meetings): add BackfillProgress admin interface"
```

---

## Phase 8: Integration Testing

### Task 8.1: Write integration test

**Files:**
- Create: `meetings/tests/test_integration.py`

**Step 1: Write integration test**

```python
"""Integration tests for backfill system."""

import pytest
from datetime import date
from unittest.mock import Mock, patch
from municipalities.models import Muni
from meetings.models import BackfillProgress, MeetingDocument
from meetings.tasks import backfill_municipality_meetings_task


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
```

**Step 2: Run integration test**

Run: `uv run pytest meetings/tests/test_integration.py -v`

Expected: PASS (2 tests)

**Step 3: Run all meetings tests**

Run: `uv run pytest meetings/ -v`

Expected: All tests pass

**Step 4: Commit**

```bash
git add meetings/tests/test_integration.py
git commit -m "test(meetings): add integration tests for backfill system"
```

---

## Phase 9: Update Webhook to Use New System

### Task 9.1: Test webhook integration

**Files:**
- Modify: `tests/municipalities/test_views.py`

**Step 1: Write test for webhook triggering new backfill**

Add to existing webhook tests:

```python
@pytest.mark.django_db
@patch("municipalities.views.django_rq.get_queue")
def test_webhook_uses_new_backfill_system(mock_get_queue):
    """Test that webhook triggers new backfill orchestrator."""
    mock_queue = Mock()
    mock_get_queue.return_value = mock_queue
    mock_job = Mock(id="job123")
    mock_queue.enqueue.return_value = mock_job

    # Create new municipality via webhook
    client = APIClient()
    response = client.post(
        "/munis/api/update/new-city/",
        {
            "name": "New City",
            "state": "CA",
            "pages": 100,
        },
        format="json",
        HTTP_AUTHORIZATION="Bearer test-secret",
    )

    assert response.status_code == 201

    # Verify backfill_municipality_meetings_task was enqueued
    mock_queue.enqueue.assert_called_once()
    call_args = mock_queue.enqueue.call_args[0]
    assert call_args[0].__name__ == "backfill_municipality_meetings_task"
```

**Step 2: Run test to verify current webhook behavior**

Run: `uv run pytest tests/municipalities/test_views.py::test_webhook_uses_new_backfill_system -v`

Expected: PASS (webhook already uses backfill_municipality_meetings_task)

**Step 3: Verify all webhook tests still pass**

Run: `uv run pytest tests/municipalities/test_views.py -k webhook -v`

Expected: All webhook tests pass

**Step 4: Commit**

```bash
git add tests/municipalities/test_views.py
git commit -m "test(municipalities): verify webhook uses new backfill system"
```

---

## Phase 10: Documentation and Cleanup

### Task 10.1: Update docstrings and comments

**Files:**
- Modify: `meetings/services.py`
- Modify: `meetings/tasks.py`

**Step 1: Add module-level docstring to tasks.py**

```python
"""
Background tasks for meeting data backfill operations.

This module implements a two-mode backfill system:

1. Full Backfill Mode:
   - Used for new municipalities or when force_full_backfill flag is set
   - Processes all historical data in batches (10 API pages per job)
   - Jobs chain together, saving progress checkpoints after each batch
   - Resumable after failures

2. Incremental Backfill Mode:
   - Used for daily webhook updates on existing municipalities
   - Fetches only ±6 months of meeting data using date filters
   - Single job, completes quickly

The orchestrator (backfill_municipality_meetings_task) determines which mode
to use based on municipality state and configuration.
"""
```

**Step 2: Update services.py docstring**

Add to top of `_backfill_document_type`:

```python
"""
Backfill meeting documents with support for batching and date filtering.

This function can operate in three modes:
1. Full backfill: Fetch all historical data (no date_range, no start_cursor)
2. Batched backfill: Fetch N pages at a time (max_pages set, returns cursor)
3. Incremental backfill: Fetch specific date range (date_range set)

Supports resuming from a cursor (start_cursor) for fault tolerance.
"""
```

**Step 3: Verify code quality**

Run: `uv run --group dev ruff check meetings/`

Expected: No errors

**Step 4: Commit**

```bash
git add meetings/services.py meetings/tasks.py
git commit -m "docs(meetings): update docstrings for backfill system"
```

---

### Task 10.2: Add README note about backfill system

**Files:**
- Modify: `docs/plans/2025-12-31-backfill-resilience-design.md`

**Step 1: Add implementation status to design doc**

Add at top after "Status: Approved":

```markdown
**Implementation Status:** ✅ Completed (2025-12-31)
**Implementation Plan:** See `docs/plans/2025-12-31-resilient-backfill.md`
```

**Step 2: Commit**

```bash
git add docs/plans/2025-12-31-backfill-resilience-design.md
git commit -m "docs: mark backfill design as implemented"
```

---

## Phase 11: Final Verification

### Task 11.1: Run full test suite

**Step 1: Run all tests with coverage**

Run: `uv run pytest --cov=meetings --cov-report=term-missing`

Expected: All tests pass, coverage >70% for meetings app

**Step 2: Run type checking**

Run: `uv run --group dev mypy meetings/`

Expected: No type errors

**Step 3: Run linting**

Run: `uv run --group dev ruff check .`

Expected: No linting errors

**Step 4: Verify migrations are up to date**

Run: `uv run python manage.py makemigrations --check --dry-run`

Expected: "No changes detected"

---

### Task 11.2: Manual testing checklist

**Step 1: Test in Django shell**

```bash
# Start shell
uv run python manage.py shell
```

```python
# Test creating progress record
from municipalities.models import Muni
from meetings.models import BackfillProgress

muni = Muni.objects.create(subdomain="test", name="Test", state="CA")
progress = BackfillProgress.objects.create(
    municipality=muni, document_type="agenda", mode="full", status="pending"
)
print(progress)  # Should display string representation

# Test admin registration
from django.contrib import admin

print(BackfillProgress in admin.site._registry)  # Should be True
```

**Step 2: Test orchestrator logic**

```python
# In Django shell
from meetings.tasks import backfill_municipality_meetings_task
from unittest.mock import patch

# Mock queue to prevent actual job enqueueing
with patch("meetings.tasks.django_rq.get_queue") as mock_queue:
    mock_queue.return_value.enqueue.return_value.id = "test-job"
    result = backfill_municipality_meetings_task(muni.id)
    print(result)  # Should show backfill started for agenda and minutes
```

**Step 3: Verify database indexes**

Run:
```sql
\c postgres
\d meetings_backfillprogress
```

Expected: Should see indexes on status and (municipality_id, status)

---

## Execution Complete

After all phases are complete, perform final verification:

1. **All tests passing**: `uv run pytest`
2. **No linting errors**: `uv run --group dev ruff check .`
3. **No type errors**: `uv run --group dev mypy .`
4. **Migrations applied**: `uv run python manage.py migrate`
5. **Admin interface works**: Start server and check `/admin/meetings/backfillprogress/`

Then use **@superpowers:finishing-a-development-branch** to complete the work.
