import logging
import re
from typing import TYPE_CHECKING

from atproto import Client, IdResolver
from django.conf import settings

from .base import NotificationSender

if TYPE_CHECKING:
    from notifications.models import NotificationChannel

logger = logging.getLogger(__name__)


class BlueskySender(NotificationSender):
    """Send notifications via Bluesky DM using AT Protocol."""

    # Bluesky handle: domain format without @ prefix
    HANDLE_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9.-]*\.[a-zA-Z]{2,}$")

    def validate_handle(self, handle: str) -> bool:
        """Validate Bluesky handle format (domain-style, no @ prefix)."""
        if not handle:
            return False
        return bool(self.HANDLE_PATTERN.match(handle))

    def _get_credentials(self) -> tuple[str, str]:
        """Get bot credentials from settings."""
        identifier = getattr(settings, "BLUESKY_BOT_HANDLE", "")
        password = getattr(settings, "BLUESKY_BOT_PASSWORD", "")
        return identifier, password

    def send(self, channel: "NotificationChannel", message: str) -> bool:
        """
        Send a DM to a Bluesky user via AT Protocol.

        Requires BLUESKY_BOT_HANDLE and BLUESKY_BOT_PASSWORD in settings.
        """
        identifier, password = self._get_credentials()
        if not identifier or not password:
            logger.error("Bluesky bot credentials not configured")
            return False

        try:
            # Create client and login
            client = Client()
            client.login(identifier, password)

            # Resolve recipient handle to DID
            resolver = IdResolver()
            recipient_did = resolver.handle.resolve(channel.handle)

            if not recipient_did:
                logger.warning(f"Failed to resolve Bluesky handle: {channel.handle}")
                return False

            # Get DM client with chat proxy
            dm_client = client.with_bsky_chat_proxy()

            # Get or create conversation
            convo = dm_client.chat.bsky.convo.get_convo_for_members(
                members=[client.me.did, recipient_did]
            )

            # Send message
            dm_client.chat.bsky.convo.send_message(
                convo_id=convo.convo.id,
                message={"text": message},
            )

            logger.info(f"Sent Bluesky DM to {channel.handle}")
            return True

        except Exception as e:
            logger.exception(f"Error sending Bluesky notification: {e}")
            return False
