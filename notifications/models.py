import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone
from model_utils.models import TimeStampedModel


class NotificationChannel(TimeStampedModel):
    """
    Represents a user's notification channel configuration.
    Each user can have one channel per platform.
    """

    PLATFORM_CHOICES = [
        ("discord", "Discord"),
        ("slack", "Slack"),
        ("bluesky", "Bluesky"),
        ("mastodon", "Mastodon"),
    ]

    MAX_FAILURES = 3

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notification_channels",
    )
    platform = models.CharField(max_length=20, choices=PLATFORM_CHOICES)
    handle = models.CharField(
        max_length=500,
        help_text="Username, handle, or webhook URL depending on platform",
    )
    is_validated = models.BooleanField(
        default=False,
        help_text="Whether the handle has been verified as reachable",
    )
    is_enabled = models.BooleanField(default=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    failure_count = models.IntegerField(default=0)

    class Meta:
        unique_together = ["user", "platform"]
        ordering = ["platform"]

    def __str__(self) -> str:
        return f"{self.platform} channel for {self.user.email}"

    def record_failure(self) -> None:
        """Record a delivery failure. Disables channel after MAX_FAILURES."""
        self.failure_count += 1
        if self.failure_count >= self.MAX_FAILURES:
            self.is_enabled = False
        self.save(update_fields=["failure_count", "is_enabled"])

    def record_success(self) -> None:
        """Record successful delivery. Resets failure count."""
        self.failure_count = 0
        self.last_used_at = timezone.now()
        self.save(update_fields=["failure_count", "last_used_at"])
