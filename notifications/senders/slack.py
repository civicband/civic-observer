import logging
from typing import TYPE_CHECKING

import httpx

from .base import NotificationSender

if TYPE_CHECKING:
    from notifications.models import NotificationChannel

logger = logging.getLogger(__name__)


class SlackSender(NotificationSender):
    """Send notifications via Slack incoming webhook."""

    WEBHOOK_PREFIX = "https://hooks.slack.com/services/"

    def validate_handle(self, handle: str) -> bool:
        """Validate Slack webhook URL format."""
        if not handle:
            return False
        return handle.startswith(self.WEBHOOK_PREFIX)

    def send(self, channel: "NotificationChannel", message: str) -> bool:
        """
        Send a message to a Slack webhook.

        The webhook URL is stored in channel.handle.
        """
        webhook_url = channel.handle

        if not self.validate_handle(webhook_url):
            logger.error(f"Invalid Slack webhook URL: {webhook_url}")
            return False

        try:
            with httpx.Client() as client:
                response = client.post(
                    webhook_url,
                    json={"text": message},
                    headers={"Content-Type": "application/json"},
                )

                if response.status_code == 200:
                    logger.info("Sent Slack notification to webhook")
                    return True
                else:
                    logger.warning(
                        f"Failed to send Slack notification: {response.status_code}"
                    )
                    return False

        except Exception as e:
            logger.exception(f"Error sending Slack notification: {e}")
            return False
