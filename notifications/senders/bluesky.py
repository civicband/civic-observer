import logging
import re
from typing import TYPE_CHECKING

import httpx
from django.conf import settings

from .base import NotificationSender

if TYPE_CHECKING:
    from notifications.models import NotificationChannel

logger = logging.getLogger(__name__)


class BlueskySender(NotificationSender):
    """Send notifications via Bluesky DM using AT Protocol."""

    # Bluesky handle: domain format without @ prefix
    HANDLE_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9.-]*\.[a-zA-Z]{2,}$")

    ATP_BASE = "https://bsky.social/xrpc"

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
            with httpx.Client() as client:
                # Authenticate
                auth_response = client.post(
                    f"{self.ATP_BASE}/com.atproto.server.createSession",
                    json={"identifier": identifier, "password": password},
                )

                if auth_response.status_code != 200:
                    logger.error(f"Bluesky auth failed: {auth_response.status_code}")
                    return False

                auth_data = auth_response.json()
                access_token = auth_data.get("accessJwt")
                sender_did = auth_data.get("did")

                headers = {"Authorization": f"Bearer {access_token}"}

                # Resolve recipient handle to DID
                resolve_response = client.get(
                    f"{self.ATP_BASE}/com.atproto.identity.resolveHandle",
                    params={"handle": channel.handle},
                )

                if resolve_response.status_code != 200:
                    logger.warning(
                        f"Failed to resolve Bluesky handle {channel.handle}: {resolve_response.status_code}"
                    )
                    return False

                recipient_did = resolve_response.json().get("did")

                # Get or create conversation
                convo_response = client.post(
                    f"{self.ATP_BASE}/chat.bsky.convo.getConvoForMembers",
                    headers=headers,
                    json={"members": [sender_did, recipient_did]},
                )

                if convo_response.status_code != 200:
                    logger.warning(
                        f"Failed to get Bluesky convo: {convo_response.status_code}"
                    )
                    return False

                convo_id = convo_response.json().get("convo", {}).get("id")

                # Send message
                send_response = client.post(
                    f"{self.ATP_BASE}/chat.bsky.convo.sendMessage",
                    headers=headers,
                    json={
                        "convoId": convo_id,
                        "message": {"text": message},
                    },
                )

                if send_response.status_code == 200:
                    logger.info(f"Sent Bluesky DM to {channel.handle}")
                    return True
                else:
                    logger.warning(
                        f"Failed to send Bluesky DM: {send_response.status_code}"
                    )
                    return False

        except Exception as e:
            logger.exception(f"Error sending Bluesky notification: {e}")
            return False
