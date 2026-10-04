"""
Resilient backfill service with checkpoint/resume capability.

This service provides robust backfilling of meeting data from civic.band
with automatic retry, progress checkpointing, and verification.
"""

import logging
import time
from typing import Any

import httpx
from django.db import transaction
from django.utils import timezone

from meetings.models import BackfillJob, MeetingPage
from meetings.services import (
    BackfillError,
    build_pages,
    bulk_upsert_pages,
    civic_band_headers,
    civic_band_table_url,
    get_or_create_document,
    group_rows_by_document,
)

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
        return civic_band_headers()

    def close(self) -> None:
        """Close the HTTP client connection."""
        self.client.close()

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - ensures client is closed."""
        self.close()

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
                    f"Timeout on attempt {attempt + 1}, retrying in {wait_time}s: {e}"
                )
                time.sleep(wait_time)

            except httpx.HTTPError as e:
                # HTTP errors (4xx, 5xx) - don't retry
                logger.error(f"HTTP error: {e}")
                raise

        # Should never reach here due to raise in loop
        raise RuntimeError("Unexpected code path in _fetch_with_retry")

    def _build_base_url(self) -> str:
        """
        Build base URL for the civic.band API.

        Returns:
            Base URL for the municipality and document type
        """
        muni = self.job.municipality
        table_name = "agendas" if self.job.document_type == "agenda" else "minutes"
        return civic_band_table_url(muni.subdomain, table_name)

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

    def _process_batch(self, rows: list[dict[str, Any]]) -> dict[str, int]:
        """
        Process a batch of rows into documents and pages.

        Grouping, validation, denormalization, and the bulk upsert are shared
        with the simple backfill path in ``meetings.services``. Documents are
        processed independently so one bad document does not fail the batch.

        Args:
            rows: List of row dictionaries from API

        Returns:
            Statistics dictionary with pages_created, pages_updated, errors
        """
        stats = {"pages_created": 0, "pages_updated": 0, "errors": 0}

        documents_map = group_rows_by_document(rows, stats)

        for (meeting_name, date_str), pages_data in documents_map.items():
            try:
                with transaction.atomic():
                    document, _created = get_or_create_document(
                        self.job.municipality,
                        meeting_name,
                        date_str,
                        self.job.document_type,
                    )

                    pages = build_pages(document, pages_data, stats)
                    if not pages:
                        continue

                    bulk_upsert_pages(pages, stats)

            except Exception as e:
                # Document processing failed - log and continue with the next
                logger.error(
                    f"Failed to process document {(meeting_name, date_str)}: {e}",
                    exc_info=True,
                )
                stats["errors"] += 1

        return stats

    def _verify_completeness(self) -> None:
        """
        Verify backfill completeness by comparing local vs API counts.

        Allows small discrepancies due to pagination timing (< 0.1% or 10 pages).
        Logs warnings if we have more data than expected.

        Raises:
            BackfillError: If significant data is missing
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

        # Check for discrepancies
        if actual != expected:
            discrepancy = abs(expected - actual)
            discrepancy_pct = (discrepancy / expected) if expected > 0 else 0

            if actual < expected:
                # Missing data - fail if significant
                error_msg = (
                    f"Missing {discrepancy} pages! Expected {expected}, got {actual}"
                )
                logger.error(error_msg)

                # Allow tiny discrepancy for pagination edge cases (< 0.1% AND < 10 pages)
                # This handles race conditions where API count changes during backfill
                if discrepancy_pct > 0.001 and discrepancy > 10:
                    self.job.status = "failed"
                    self.job.last_error = error_msg
                    self.job.save(update_fields=["status", "last_error", "modified"])
                    raise BackfillError(error_msg)
                else:
                    logger.warning(
                        f"Minor discrepancy tolerated: {discrepancy} pages missing "
                        f"({discrepancy_pct:.3%})"
                    )
            else:
                # More data than expected - log warning but don't fail
                # This can happen if pages were added to API during backfill
                # or if data was created by other processes
                logger.warning(
                    f"Found {actual} pages but API reports {expected} "
                    f"(+{discrepancy} extra pages, {discrepancy_pct:.2%})"
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
        data = self._fetch_with_retry(f"{base_url}?_size=1")

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
            data = self._fetch_with_retry(url)
            count += len(data.get("rows", []))

            next_cursor = data.get("next")
            if next_cursor:
                url = f"{self._build_base_url()}?_size={self.batch_size}&_next={next_cursor}"
            else:
                break

        return count

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
            url: str | None = self._build_initial_url()

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
