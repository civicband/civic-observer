"""
Resilient backfill service with checkpoint/resume capability.

This service provides robust backfilling of meeting data from civic.band
with automatic retry, progress checkpointing, and verification.
"""

import logging
import time  # noqa: F401 - Used in Tasks 3-8
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
