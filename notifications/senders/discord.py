import logging
import re
from typing import TYPE_CHECKING

import httpx
from django.conf import settings

from .base import NotificationSender

if TYPE_CHECKING:
    from notifications.models import NotificationChannel

logger = logging.getLogger(__name__)


class DiscordSender(NotificationSender):
    """Send notifications via Discord DM using bot API."""

    # Discord username pattern: 3-32 chars, optional #0000-#9999 discriminator
    USERNAME_PATTERN = re.compile(r"^[a-zA-Z0-9_.]{3,32}(#\d{4})?$")

    def validate_handle(self, handle: str) -> bool:
        """Validate Discord username format."""
        if not handle:
            return False
        return bool(self.USERNAME_PATTERN.match(handle))

    def send(self, channel: "NotificationChannel", message: str) -> bool:
        """
        Send a DM to a Discord user.

        Requires DISCORD_BOT_TOKEN in settings.
        User must share a server with the bot and have DMs enabled.
        """
        bot_token = getattr(settings, "DISCORD_BOT_TOKEN", None)
        if not bot_token:
            logger.error("DISCORD_BOT_TOKEN not configured")
            return False

        try:
            with httpx.Client() as client:
                # First, we need to get the user ID from the username
                # This requires the bot to have access to the user
                # For now, assume handle contains the user ID for DM channel creation
                # In production, you'd look up the user ID from username

                # Create DM channel
                dm_response = client.post(
                    "https://discord.com/api/v10/users/@me/channels",
                    headers={"Authorization": f"Bot {bot_token}"},
                    json={
                        "recipient_id": channel.handle
                    },  # Assume handle is user ID for now
                )

                if dm_response.status_code != 200:
                    logger.warning(
                        f"Failed to create DM channel for {channel.handle}: {dm_response.status_code}"
                    )
                    return False

                dm_channel_id = dm_response.json().get("id")

                # Send message to DM channel
                msg_response = client.post(
                    f"https://discord.com/api/v10/channels/{dm_channel_id}/messages",
                    headers={"Authorization": f"Bot {bot_token}"},
                    json={"content": message},
                )

                if msg_response.status_code == 200:
                    logger.info(f"Sent Discord DM to {channel.handle}")
                    return True
                else:
                    logger.warning(
                        f"Failed to send Discord DM: {msg_response.status_code}"
                    )
                    return False

        except Exception as e:
            logger.exception(f"Error sending Discord notification: {e}")
            return False
