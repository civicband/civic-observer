import logging
from typing import TYPE_CHECKING

from .senders import get_sender

if TYPE_CHECKING:
    from notifications.models import NotificationChannel
    from searches.models import SavedSearch

logger = logging.getLogger(__name__)


def dispatch_notification(channel: "NotificationChannel", message: str) -> bool:
    """
    Dispatch a notification to a single channel.

    Args:
        channel: The notification channel to send to
        message: The message content

    Returns:
        True if successful, False otherwise
    """
    if not channel.is_enabled:
        logger.debug(
            f"Skipping disabled channel {channel.platform} for {channel.user.email}"
        )
        return False

    sender = get_sender(channel.platform)
    if not sender:
        logger.error(f"No sender found for platform: {channel.platform}")
        return False

    try:
        success = sender.send(channel, message)

        if success:
            channel.record_success()
            return True
        else:
            channel.record_failure()
            return False

    except Exception as e:
        logger.exception(f"Error dispatching to {channel.platform}: {e}")
        channel.record_failure()
        return False


def dispatch_to_all_channels(
    saved_search: "SavedSearch",
    message: str,
) -> list[dict]:
    """
    Dispatch notification to all effective channels for a saved search.

    Args:
        saved_search: The saved search triggering the notification
        message: The message content

    Returns:
        List of result dicts with platform, success, and error keys
    """
    channels = saved_search.get_effective_channels()
    results = []

    for channel in channels:
        success = dispatch_notification(channel, message)
        results.append(
            {
                "platform": channel.platform,
                "success": success,
                "channel_id": str(channel.id),
            }
        )

    return results
