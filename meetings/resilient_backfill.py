"""
Resilient backfill service with checkpoint/resume capability.

This service provides robust backfilling of meeting data from civic.band
with automatic retry, progress checkpointing, and verification.
"""

import logging

import httpx
from django.conf import settings

from meetings.models import BackfillJob

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
