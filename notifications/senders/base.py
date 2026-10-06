import time
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from notifications.models import NotificationChannel

# Keep a hard cap on outbound calls so a slow or hanging platform can't stall a
# worker. Rate-limit responses (429) are retried once, honoring Retry-After.
HTTP_TIMEOUT_SECONDS = 10.0
RETRY_AFTER_CAP_SECONDS = 10.0


def parse_retry_after(response: httpx.Response, default: float = 1.0) -> float:
    """Return the Retry-After delay in seconds, capped, falling back to default."""
    value = response.headers.get("Retry-After")
    if not value:
        return default
    try:
        return min(float(value), RETRY_AFTER_CAP_SECONDS)
    except (TypeError, ValueError):
        return default


class NotificationSender(ABC):
    """Abstract base class for notification channel senders."""

    def _post_with_retry(
        self, client: httpx.Client, url: str, **kwargs: Any
    ) -> httpx.Response:
        """POST, retrying once on HTTP 429 after the Retry-After delay.

        A transient rate limit should not be recorded as a delivery failure
        (which counts toward disabling the channel), so it gets one retry.
        """
        response = client.post(url, **kwargs)
        if response.status_code == 429:
            time.sleep(parse_retry_after(response))
            response = client.post(url, **kwargs)
        return response

    @abstractmethod
    def send(self, channel: "NotificationChannel", message: str) -> bool:
        """
        Send a notification message to the channel.

        Args:
            channel: The NotificationChannel to send to
            message: The message content to send

        Returns:
            True if send was successful, False otherwise
        """
        pass

    @abstractmethod
    def validate_handle(self, handle: str) -> bool:
        """
        Validate that a handle is in the correct format for this platform.

        Args:
            handle: The handle/username/URL to validate

        Returns:
            True if valid, False otherwise
        """
        pass
