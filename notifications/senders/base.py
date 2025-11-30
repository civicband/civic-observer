from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from notifications.models import NotificationChannel


class NotificationSender(ABC):
    """Abstract base class for notification channel senders."""

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
