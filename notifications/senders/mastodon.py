import logging
import re
from typing import TYPE_CHECKING

import httpx
from django.conf import settings

from .base import NotificationSender

if TYPE_CHECKING:
    from notifications.models import NotificationChannel

logger = logging.getLogger(__name__)


class MastodonSender(NotificationSender):
    """Send notifications via Mastodon DM (direct visibility post)."""

    # Mastodon handle: @user@instance or user@instance
    HANDLE_PATTERN = re.compile(r"^@?([a-zA-Z0-9_]+)@([a-zA-Z0-9.-]+\.[a-zA-Z]{2,})$")

    def validate_handle(self, handle: str) -> bool:
        """Validate Mastodon handle format (@user@instance.tld)."""
        if not handle:
            return False
        return bool(self.HANDLE_PATTERN.match(handle))

    def _parse_handle(self, handle: str) -> tuple[str, str]:
        """Parse handle into (username, instance)."""
        match = self.HANDLE_PATTERN.match(handle)
        if match:
            return match.group(1), match.group(2)
        return "", ""

    def send(self, channel: "NotificationChannel", message: str) -> bool:
        """
        Send a DM to a Mastodon user.

        Uses direct visibility status to send a DM mentioning the user.
        Requires MASTODON_ACCESS_TOKEN and MASTODON_INSTANCE_URL in settings.
        """
        access_token = getattr(settings, "MASTODON_ACCESS_TOKEN", "")
        instance_url = getattr(
            settings, "MASTODON_INSTANCE_URL", "https://mastodon.social"
        )

        if not access_token:
            logger.error("MASTODON_ACCESS_TOKEN not configured")
            return False

        username, instance = self._parse_handle(channel.handle)
        if not username:
            logger.error(f"Invalid Mastodon handle: {channel.handle}")
            return False

        # Construct the full mention
        mention = f"@{username}@{instance}"

        try:
            with httpx.Client() as client:
                # Post a status with direct visibility (DM)
                response = client.post(
                    f"{instance_url}/api/v1/statuses",
                    headers={"Authorization": f"Bearer {access_token}"},
                    json={
                        "status": f"{mention} {message}",
                        "visibility": "direct",
                    },
                )

                if response.status_code == 200:
                    logger.info(f"Sent Mastodon DM to {channel.handle}")
                    return True
                else:
                    logger.warning(
                        f"Failed to send Mastodon DM: {response.status_code}"
                    )
                    return False

        except Exception as e:
            logger.exception(f"Error sending Mastodon notification: {e}")
            return False
