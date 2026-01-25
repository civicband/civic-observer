"""
Resilient backfill service with checkpoint/resume capability.

This service provides robust backfilling of meeting data from civic.band
with automatic retry, progress checkpointing, and verification.
"""

import logging
import time  # noqa: F401 - Used in Tasks 3-8
from datetime import date
from typing import Any  # noqa: F401 - Used in Tasks 3-8

import httpx
from django.conf import settings
from django.db import transaction  # noqa: F401 - Used in Tasks 3-8
from django.utils import timezone  # noqa: F401 - Used in Tasks 3-8

from meetings.models import (  # noqa: F401 - Used in Tasks 3-8
    BackfillJob,
    MeetingDocument,
    MeetingPage,
)
from meetings.services import BackfillError  # noqa: F401 - Used in Tasks 3-8

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
        documents_map: dict[tuple[str, str], list[dict[str, Any]]] = {}

        for row in rows:
            try:
                meeting_name = row.get("meeting", "")
                date_str = row.get("date", "")
                page_id = row.get("id", "")

                if not meeting_name or not date_str or not page_id:
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
