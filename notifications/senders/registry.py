from .base import NotificationSender
from .bluesky import BlueskySender
from .discord import DiscordSender
from .mastodon import MastodonSender
from .slack import SlackSender


class SenderRegistry:
    """Registry for notification sender instances."""

    _instances: dict[str, NotificationSender] = {}

    SENDER_MAP: dict[str, type[NotificationSender]] = {
        "discord": DiscordSender,
        "slack": SlackSender,
        "bluesky": BlueskySender,
        "mastodon": MastodonSender,
    }

    @classmethod
    def get(cls, platform: str) -> NotificationSender | None:
        """Get sender instance for platform. Returns None if not found."""
        if platform not in cls.SENDER_MAP:
            return None

        if platform not in cls._instances:
            cls._instances[platform] = cls.SENDER_MAP[platform]()

        return cls._instances[platform]


def get_sender(platform: str) -> NotificationSender | None:
    """Convenience function to get sender for platform."""
    return SenderRegistry.get(platform)
