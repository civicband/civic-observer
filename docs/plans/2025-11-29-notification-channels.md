# Notification Channels Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Expand notification delivery beyond email to include Discord, Slack, Bluesky, and Mastodon.

**Architecture:** Abstract notification delivery behind a common interface. Each channel gets a dedicated sender class. Users configure channels at account level with per-search overrides. Failed deliveries retry 3x then disable with email notification.

**Tech Stack:** Django, HTMX for UI, httpx for API calls, existing django-rq for background tasks.

---

## Task 1: Create NotificationChannel Model

**Files:**
- Create: `notifications/__init__.py`
- Create: `notifications/models.py`
- Create: `notifications/apps.py`
- Create: `notifications/admin.py`
- Modify: `config/settings/base.py` (add to INSTALLED_APPS)
- Create: `tests/notifications/__init__.py`
- Create: `tests/notifications/test_models.py`

**Step 1: Create the notifications app directory structure**

```bash
mkdir -p notifications
mkdir -p tests/notifications
touch notifications/__init__.py
touch tests/notifications/__init__.py
```

**Step 2: Write the failing test for NotificationChannel model**

Create `tests/notifications/test_models.py`:

```python
import pytest

from notifications.models import NotificationChannel


@pytest.mark.django_db
class TestNotificationChannelModel:
    def test_create_discord_channel(self):
        """Test creating a Discord notification channel."""
        from tests.factories import UserFactory

        user = UserFactory()
        channel = NotificationChannel.objects.create(
            user=user,
            platform="discord",
            handle="username#1234",
        )

        assert channel.id is not None
        assert channel.platform == "discord"
        assert channel.handle == "username#1234"
        assert channel.is_validated is False
        assert channel.is_enabled is True
        assert channel.failure_count == 0

    def test_create_slack_channel(self):
        """Test creating a Slack notification channel with webhook URL."""
        from tests.factories import UserFactory

        user = UserFactory()
        channel = NotificationChannel.objects.create(
            user=user,
            platform="slack",
            handle="https://hooks.slack.com/services/T00/B00/xxx",
        )

        assert channel.platform == "slack"
        assert "hooks.slack.com" in channel.handle

    def test_create_bluesky_channel(self):
        """Test creating a Bluesky notification channel."""
        from tests.factories import UserFactory

        user = UserFactory()
        channel = NotificationChannel.objects.create(
            user=user,
            platform="bluesky",
            handle="user.bsky.social",
        )

        assert channel.platform == "bluesky"

    def test_create_mastodon_channel(self):
        """Test creating a Mastodon notification channel."""
        from tests.factories import UserFactory

        user = UserFactory()
        channel = NotificationChannel.objects.create(
            user=user,
            platform="mastodon",
            handle="@user@mastodon.social",
        )

        assert channel.platform == "mastodon"

    def test_platform_choices(self):
        """Test that only valid platforms are allowed."""
        from django.core.exceptions import ValidationError
        from tests.factories import UserFactory

        user = UserFactory()
        channel = NotificationChannel(
            user=user,
            platform="invalid_platform",
            handle="test",
        )

        with pytest.raises(ValidationError):
            channel.full_clean()

    def test_unique_platform_per_user(self):
        """Test that a user can only have one channel per platform."""
        from django.db import IntegrityError
        from tests.factories import UserFactory

        user = UserFactory()
        NotificationChannel.objects.create(
            user=user,
            platform="discord",
            handle="user1#1234",
        )

        with pytest.raises(IntegrityError):
            NotificationChannel.objects.create(
                user=user,
                platform="discord",
                handle="user2#5678",
            )

    def test_str_representation(self):
        """Test string representation of channel."""
        from tests.factories import UserFactory

        user = UserFactory()
        channel = NotificationChannel.objects.create(
            user=user,
            platform="discord",
            handle="testuser#1234",
        )

        assert "discord" in str(channel).lower()
        assert user.email in str(channel)

    def test_increment_failure_count(self):
        """Test incrementing failure count."""
        from tests.factories import UserFactory

        user = UserFactory()
        channel = NotificationChannel.objects.create(
            user=user,
            platform="discord",
            handle="user#1234",
        )

        channel.record_failure()
        channel.refresh_from_db()

        assert channel.failure_count == 1

    def test_auto_disable_after_max_failures(self):
        """Test channel is disabled after 3 failures."""
        from tests.factories import UserFactory

        user = UserFactory()
        channel = NotificationChannel.objects.create(
            user=user,
            platform="discord",
            handle="user#1234",
        )

        channel.record_failure()
        channel.record_failure()
        channel.record_failure()
        channel.refresh_from_db()

        assert channel.failure_count == 3
        assert channel.is_enabled is False

    def test_record_success_resets_failure_count(self):
        """Test successful delivery resets failure count."""
        from tests.factories import UserFactory

        user = UserFactory()
        channel = NotificationChannel.objects.create(
            user=user,
            platform="discord",
            handle="user#1234",
            failure_count=2,
        )

        channel.record_success()
        channel.refresh_from_db()

        assert channel.failure_count == 0
        assert channel.last_used_at is not None
```

**Step 3: Run test to verify it fails**

Run: `uv run pytest tests/notifications/test_models.py -v`
Expected: FAIL with "ModuleNotFoundError: No module named 'notifications'"

**Step 4: Create the apps.py file**

Create `notifications/apps.py`:

```python
from django.apps import AppConfig


class NotificationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "notifications"
```

**Step 5: Create the model**

Create `notifications/models.py`:

```python
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
```

**Step 6: Add to INSTALLED_APPS**

Modify `config/settings/base.py`, add to INSTALLED_APPS list:

```python
INSTALLED_APPS = [
    # ... existing apps ...
    "notifications",
]
```

**Step 7: Create and run migrations**

Run: `uv run python manage.py makemigrations notifications`
Run: `uv run python manage.py migrate`

**Step 8: Run tests to verify they pass**

Run: `uv run pytest tests/notifications/test_models.py -v`
Expected: All tests PASS

**Step 9: Create admin interface**

Create `notifications/admin.py`:

```python
from django.contrib import admin

from .models import NotificationChannel


@admin.register(NotificationChannel)
class NotificationChannelAdmin(admin.ModelAdmin):
    list_display = [
        "user",
        "platform",
        "handle",
        "is_validated",
        "is_enabled",
        "failure_count",
        "last_used_at",
    ]
    list_filter = ["platform", "is_validated", "is_enabled"]
    search_fields = ["user__email", "handle"]
    readonly_fields = ["id", "created", "modified", "last_used_at"]
```

**Step 10: Commit**

```bash
git add notifications/ tests/notifications/ config/settings/base.py
git commit -m "feat(notifications): add NotificationChannel model

- Create notifications app with NotificationChannel model
- Support Discord, Slack, Bluesky, Mastodon platforms
- Unique constraint per user per platform
- Failure tracking with auto-disable after 3 failures
- Admin interface for channel management"
```

---

## Task 2: Create NotificationChannelFactory

**Files:**
- Modify: `tests/factories.py`

**Step 1: Write test that uses the factory**

Add to `tests/notifications/test_models.py`:

```python
class TestNotificationChannelFactory:
    def test_factory_creates_valid_channel(self):
        """Test factory creates valid NotificationChannel."""
        from tests.factories import NotificationChannelFactory

        channel = NotificationChannelFactory()

        assert channel.id is not None
        assert channel.user is not None
        assert channel.platform in ["discord", "slack", "bluesky", "mastodon"]

    def test_factory_with_specific_platform(self):
        """Test factory with specific platform."""
        from tests.factories import NotificationChannelFactory

        channel = NotificationChannelFactory(platform="bluesky")

        assert channel.platform == "bluesky"
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/notifications/test_models.py::TestNotificationChannelFactory -v`
Expected: FAIL with "ImportError: cannot import name 'NotificationChannelFactory'"

**Step 3: Create the factory**

Add to `tests/factories.py`:

```python
from notifications.models import NotificationChannel


class NotificationChannelFactory(DjangoModelFactory):
    class Meta:
        model = NotificationChannel

    user = factory.SubFactory(UserFactory)  # type: ignore
    platform = factory.Iterator(["discord", "slack", "bluesky", "mastodon"])  # type: ignore
    handle = factory.LazyAttribute(  # type: ignore
        lambda obj: {
            "discord": f"user{obj.user.id}#1234",
            "slack": f"https://hooks.slack.com/services/T00/B00/{obj.user.id}",
            "bluesky": f"user{obj.user.id}.bsky.social",
            "mastodon": f"@user{obj.user.id}@mastodon.social",
        }.get(obj.platform, f"user{obj.user.id}")
    )
    is_validated = False
    is_enabled = True
    failure_count = 0
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/notifications/test_models.py::TestNotificationChannelFactory -v`
Expected: PASS

**Step 5: Commit**

```bash
git add tests/factories.py tests/notifications/test_models.py
git commit -m "test(notifications): add NotificationChannelFactory"
```

---

## Task 3: Add notification_channels JSONField to SavedSearch

**Files:**
- Modify: `searches/models.py`
- Create: `tests/notifications/test_saved_search_channels.py`

**Step 1: Write failing test**

Create `tests/notifications/test_saved_search_channels.py`:

```python
import pytest

from searches.models import SavedSearch


@pytest.mark.django_db
class TestSavedSearchNotificationChannels:
    def test_default_notification_channels_is_empty_dict(self):
        """Test notification_channels defaults to empty dict."""
        from tests.factories import SavedSearchFactory

        saved_search = SavedSearchFactory()

        assert saved_search.notification_channels == {}

    def test_can_set_channel_overrides(self):
        """Test can set channel overrides."""
        from tests.factories import SavedSearchFactory

        saved_search = SavedSearchFactory()
        saved_search.notification_channels = {
            "channels": ["discord", "email"],
            "enabled": True,
        }
        saved_search.save()

        saved_search.refresh_from_db()
        assert saved_search.notification_channels["channels"] == ["discord", "email"]

    def test_get_effective_channels_with_override(self):
        """Test getting effective channels when override is set."""
        from tests.factories import NotificationChannelFactory, SavedSearchFactory

        saved_search = SavedSearchFactory()
        NotificationChannelFactory(
            user=saved_search.user,
            platform="discord",
            is_enabled=True,
        )
        NotificationChannelFactory(
            user=saved_search.user,
            platform="slack",
            is_enabled=True,
        )

        # Set override to only use discord
        saved_search.notification_channels = {"channels": ["discord"]}
        saved_search.save()

        channels = saved_search.get_effective_channels()
        assert len(channels) == 1
        assert channels[0].platform == "discord"

    def test_get_effective_channels_without_override(self):
        """Test getting all enabled channels when no override."""
        from tests.factories import NotificationChannelFactory, SavedSearchFactory

        saved_search = SavedSearchFactory()
        NotificationChannelFactory(
            user=saved_search.user,
            platform="discord",
            is_enabled=True,
        )
        NotificationChannelFactory(
            user=saved_search.user,
            platform="slack",
            is_enabled=True,
        )

        channels = saved_search.get_effective_channels()
        assert len(channels) == 2

    def test_get_effective_channels_excludes_disabled(self):
        """Test disabled channels are excluded."""
        from tests.factories import NotificationChannelFactory, SavedSearchFactory

        saved_search = SavedSearchFactory()
        NotificationChannelFactory(
            user=saved_search.user,
            platform="discord",
            is_enabled=True,
        )
        NotificationChannelFactory(
            user=saved_search.user,
            platform="slack",
            is_enabled=False,
        )

        channels = saved_search.get_effective_channels()
        assert len(channels) == 1
        assert channels[0].platform == "discord"
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/notifications/test_saved_search_channels.py -v`
Expected: FAIL with "AttributeError: 'SavedSearch' object has no attribute 'notification_channels'"

**Step 3: Add the field and method to SavedSearch**

Modify `searches/models.py`, add to SavedSearch class:

```python
class SavedSearch(TimeStampedModel):
    # ... existing fields ...

    notification_channels = models.JSONField(
        default=dict,
        blank=True,
        help_text='Channel overrides: {"channels": ["discord", "email"]}',
    )

    # ... existing methods ...

    def get_effective_channels(self):
        """
        Get the notification channels to use for this saved search.

        If notification_channels has a "channels" key, use only those platforms.
        Otherwise, return all enabled channels for the user.
        """
        from notifications.models import NotificationChannel

        user_channels = NotificationChannel.objects.filter(
            user=self.user,
            is_enabled=True,
        )

        override = self.notification_channels.get("channels")
        if override:
            return list(user_channels.filter(platform__in=override))

        return list(user_channels)
```

**Step 4: Create and run migration**

Run: `uv run python manage.py makemigrations searches`
Run: `uv run python manage.py migrate`

**Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/notifications/test_saved_search_channels.py -v`
Expected: All tests PASS

**Step 6: Commit**

```bash
git add searches/models.py searches/migrations/
git commit -m "feat(searches): add notification_channels override to SavedSearch

- JSONField for per-search channel overrides
- get_effective_channels() method to resolve which channels to use
- Defaults to all enabled user channels when no override"
```

---

## Task 4: Create Abstract NotificationSender Interface

**Files:**
- Create: `notifications/senders/__init__.py`
- Create: `notifications/senders/base.py`
- Create: `tests/notifications/test_senders.py`

**Step 1: Write failing test**

Create `tests/notifications/test_senders.py`:

```python
import pytest

from notifications.senders.base import NotificationSender


class TestNotificationSenderInterface:
    def test_base_class_is_abstract(self):
        """Test base sender class cannot be instantiated."""
        with pytest.raises(TypeError):
            NotificationSender()  # type: ignore

    def test_subclass_must_implement_send(self):
        """Test subclasses must implement send method."""

        class IncompleteSender(NotificationSender):
            def validate_handle(self, handle: str) -> bool:
                return True

        with pytest.raises(TypeError):
            IncompleteSender()  # type: ignore

    def test_subclass_must_implement_validate_handle(self):
        """Test subclasses must implement validate_handle method."""

        class IncompleteSender(NotificationSender):
            def send(self, channel, message: str) -> bool:
                return True

        with pytest.raises(TypeError):
            IncompleteSender()  # type: ignore

    def test_complete_subclass_can_be_instantiated(self):
        """Test complete subclass can be instantiated."""

        class CompleteSender(NotificationSender):
            def send(self, channel, message: str) -> bool:
                return True

            def validate_handle(self, handle: str) -> bool:
                return True

        sender = CompleteSender()
        assert sender is not None
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/notifications/test_senders.py -v`
Expected: FAIL with "ModuleNotFoundError: No module named 'notifications.senders'"

**Step 3: Create the base sender class**

Create `notifications/senders/__init__.py`:

```python
from .base import NotificationSender

__all__ = ["NotificationSender"]
```

Create `notifications/senders/base.py`:

```python
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
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/notifications/test_senders.py -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add notifications/senders/
git commit -m "feat(notifications): add abstract NotificationSender base class

- Abstract methods for send() and validate_handle()
- Foundation for platform-specific sender implementations"
```

---

## Task 5: Implement Discord Sender

**Files:**
- Create: `notifications/senders/discord.py`
- Modify: `notifications/senders/__init__.py`
- Create: `tests/notifications/test_discord_sender.py`

**Step 1: Write failing test**

Create `tests/notifications/test_discord_sender.py`:

```python
import pytest
from unittest.mock import patch, MagicMock

from notifications.senders.discord import DiscordSender


class TestDiscordSenderValidation:
    def test_valid_discord_username(self):
        """Test valid Discord username format."""
        sender = DiscordSender()

        # Modern Discord usernames (no discriminator)
        assert sender.validate_handle("username") is True
        assert sender.validate_handle("user_name") is True
        assert sender.validate_handle("user.name") is True

    def test_valid_discord_username_with_discriminator(self):
        """Test valid Discord username with legacy discriminator."""
        sender = DiscordSender()

        assert sender.validate_handle("username#1234") is True
        assert sender.validate_handle("user#0001") is True

    def test_invalid_discord_username(self):
        """Test invalid Discord username formats."""
        sender = DiscordSender()

        assert sender.validate_handle("") is False
        assert sender.validate_handle("ab") is False  # Too short
        assert sender.validate_handle("user#abc") is False  # Invalid discriminator
        assert sender.validate_handle("user#12345") is False  # Discriminator too long


@pytest.mark.django_db
class TestDiscordSenderSend:
    @patch("notifications.senders.discord.httpx.Client")
    def test_send_success(self, mock_client_class):
        """Test successful Discord message send."""
        from tests.factories import NotificationChannelFactory

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"id": "123"}
        mock_client.post.return_value = mock_response
        mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

        channel = NotificationChannelFactory(platform="discord", handle="testuser#1234")
        sender = DiscordSender()

        result = sender.send(channel, "Test notification message")

        assert result is True

    @patch("notifications.senders.discord.httpx.Client")
    def test_send_failure(self, mock_client_class):
        """Test failed Discord message send."""
        from tests.factories import NotificationChannelFactory

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.raise_for_status.side_effect = Exception("Forbidden")
        mock_client.post.return_value = mock_response
        mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

        channel = NotificationChannelFactory(platform="discord", handle="testuser#1234")
        sender = DiscordSender()

        result = sender.send(channel, "Test notification message")

        assert result is False
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/notifications/test_discord_sender.py -v`
Expected: FAIL with "ModuleNotFoundError: No module named 'notifications.senders.discord'"

**Step 3: Implement Discord sender**

Create `notifications/senders/discord.py`:

```python
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

    # Discord username pattern: 2-32 chars, optional #0000-#9999 discriminator
    USERNAME_PATTERN = re.compile(r"^[a-zA-Z0-9_.]{2,32}(#\d{4})?$")

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
```

**Step 4: Update __init__.py**

Modify `notifications/senders/__init__.py`:

```python
from .base import NotificationSender
from .discord import DiscordSender

__all__ = ["NotificationSender", "DiscordSender"]
```

**Step 5: Add httpx dependency if not present**

Run: `uv add httpx`

**Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/notifications/test_discord_sender.py -v`
Expected: All tests PASS

**Step 7: Commit**

```bash
git add notifications/senders/ tests/notifications/test_discord_sender.py pyproject.toml uv.lock
git commit -m "feat(notifications): add Discord sender implementation

- Username validation with discriminator support
- DM sending via Discord Bot API
- Error handling and logging"
```

---

## Task 6: Implement Slack Sender

**Files:**
- Create: `notifications/senders/slack.py`
- Modify: `notifications/senders/__init__.py`
- Create: `tests/notifications/test_slack_sender.py`

**Step 1: Write failing test**

Create `tests/notifications/test_slack_sender.py`:

```python
import pytest
from unittest.mock import patch, MagicMock

from notifications.senders.slack import SlackSender


class TestSlackSenderValidation:
    def test_valid_slack_webhook_url(self):
        """Test valid Slack webhook URL."""
        sender = SlackSender()

        assert (
            sender.validate_handle(
                "https://hooks.slack.com/services/T00000000/B00000000/XXXXXXXXXXXXXXXXXXXXXXXX"
            )
            is True
        )

    def test_invalid_slack_webhook_url(self):
        """Test invalid Slack webhook URLs."""
        sender = SlackSender()

        assert sender.validate_handle("") is False
        assert sender.validate_handle("not-a-url") is False
        assert sender.validate_handle("https://example.com/webhook") is False
        assert (
            sender.validate_handle("http://hooks.slack.com/services/xxx") is False
        )  # Must be HTTPS


@pytest.mark.django_db
class TestSlackSenderSend:
    @patch("notifications.senders.slack.httpx.Client")
    def test_send_success(self, mock_client_class):
        """Test successful Slack webhook send."""
        from tests.factories import NotificationChannelFactory

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_client.post.return_value = mock_response
        mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

        channel = NotificationChannelFactory(
            platform="slack",
            handle="https://hooks.slack.com/services/T00/B00/xxx",
        )
        sender = SlackSender()

        result = sender.send(channel, "Test notification message")

        assert result is True
        mock_client.post.assert_called_once()

    @patch("notifications.senders.slack.httpx.Client")
    def test_send_failure(self, mock_client_class):
        """Test failed Slack webhook send."""
        from tests.factories import NotificationChannelFactory

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_client.post.return_value = mock_response
        mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

        channel = NotificationChannelFactory(
            platform="slack",
            handle="https://hooks.slack.com/services/T00/B00/xxx",
        )
        sender = SlackSender()

        result = sender.send(channel, "Test notification message")

        assert result is False
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/notifications/test_slack_sender.py -v`
Expected: FAIL with "ModuleNotFoundError"

**Step 3: Implement Slack sender**

Create `notifications/senders/slack.py`:

```python
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
                    logger.info(f"Sent Slack notification to webhook")
                    return True
                else:
                    logger.warning(
                        f"Failed to send Slack notification: {response.status_code}"
                    )
                    return False

        except Exception as e:
            logger.exception(f"Error sending Slack notification: {e}")
            return False
```

**Step 4: Update __init__.py**

```python
from .base import NotificationSender
from .discord import DiscordSender
from .slack import SlackSender

__all__ = ["NotificationSender", "DiscordSender", "SlackSender"]
```

**Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/notifications/test_slack_sender.py -v`
Expected: All tests PASS

**Step 6: Commit**

```bash
git add notifications/senders/ tests/notifications/test_slack_sender.py
git commit -m "feat(notifications): add Slack webhook sender

- Webhook URL validation
- Simple JSON payload posting
- Error handling and logging"
```

---

## Task 7: Implement Bluesky Sender

**Files:**
- Create: `notifications/senders/bluesky.py`
- Modify: `notifications/senders/__init__.py`
- Create: `tests/notifications/test_bluesky_sender.py`

**Step 1: Write failing test**

Create `tests/notifications/test_bluesky_sender.py`:

```python
import pytest
from unittest.mock import patch, MagicMock

from notifications.senders.bluesky import BlueskySender


class TestBlueskySenderValidation:
    def test_valid_bluesky_handle(self):
        """Test valid Bluesky handle formats."""
        sender = BlueskySender()

        assert sender.validate_handle("user.bsky.social") is True
        assert sender.validate_handle("username.bsky.social") is True
        assert sender.validate_handle("user-name.bsky.social") is True
        assert sender.validate_handle("custom.domain.com") is True

    def test_invalid_bluesky_handle(self):
        """Test invalid Bluesky handle formats."""
        sender = BlueskySender()

        assert sender.validate_handle("") is False
        assert sender.validate_handle("nodomain") is False
        assert sender.validate_handle("@user.bsky.social") is False  # No @ prefix


@pytest.mark.django_db
class TestBlueskySenderSend:
    @patch("notifications.senders.bluesky.httpx.Client")
    def test_send_success(self, mock_client_class):
        """Test successful Bluesky DM send."""
        from tests.factories import NotificationChannelFactory

        mock_client = MagicMock()

        # Mock auth response
        auth_response = MagicMock()
        auth_response.status_code = 200
        auth_response.json.return_value = {
            "accessJwt": "test_token",
            "did": "did:plc:xxx",
        }

        # Mock resolve handle response
        resolve_response = MagicMock()
        resolve_response.status_code = 200
        resolve_response.json.return_value = {"did": "did:plc:recipient"}

        # Mock get convo response
        convo_response = MagicMock()
        convo_response.status_code = 200
        convo_response.json.return_value = {"convo": {"id": "convo123"}}

        # Mock send message response
        send_response = MagicMock()
        send_response.status_code = 200

        mock_client.post.side_effect = [auth_response, convo_response, send_response]
        mock_client.get.return_value = resolve_response

        mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

        channel = NotificationChannelFactory(
            platform="bluesky",
            handle="recipient.bsky.social",
        )
        sender = BlueskySender()

        with patch.object(
            sender, "_get_credentials", return_value=("bot.bsky.social", "password")
        ):
            result = sender.send(channel, "Test notification message")

        assert result is True
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/notifications/test_bluesky_sender.py -v`
Expected: FAIL with "ModuleNotFoundError"

**Step 3: Implement Bluesky sender**

Create `notifications/senders/bluesky.py`:

```python
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
```

**Step 4: Update __init__.py**

```python
from .base import NotificationSender
from .bluesky import BlueskySender
from .discord import DiscordSender
from .slack import SlackSender

__all__ = ["NotificationSender", "BlueskySender", "DiscordSender", "SlackSender"]
```

**Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/notifications/test_bluesky_sender.py -v`
Expected: All tests PASS

**Step 6: Commit**

```bash
git add notifications/senders/ tests/notifications/test_bluesky_sender.py
git commit -m "feat(notifications): add Bluesky sender via AT Protocol

- Handle validation (domain format)
- DM sending via chat.bsky.convo API
- Session-based authentication"
```

---

## Task 8: Implement Mastodon Sender

**Files:**
- Create: `notifications/senders/mastodon.py`
- Modify: `notifications/senders/__init__.py`
- Create: `tests/notifications/test_mastodon_sender.py`

**Step 1: Write failing test**

Create `tests/notifications/test_mastodon_sender.py`:

```python
import pytest
from unittest.mock import patch, MagicMock

from notifications.senders.mastodon import MastodonSender


class TestMastodonSenderValidation:
    def test_valid_mastodon_handle(self):
        """Test valid Mastodon handle formats."""
        sender = MastodonSender()

        assert sender.validate_handle("@user@mastodon.social") is True
        assert sender.validate_handle("@username@instance.example.com") is True
        assert (
            sender.validate_handle("user@mastodon.social") is True
        )  # Without leading @

    def test_invalid_mastodon_handle(self):
        """Test invalid Mastodon handle formats."""
        sender = MastodonSender()

        assert sender.validate_handle("") is False
        assert sender.validate_handle("@user") is False  # No instance
        assert sender.validate_handle("user") is False


@pytest.mark.django_db
class TestMastodonSenderSend:
    @patch("notifications.senders.mastodon.httpx.Client")
    def test_send_success(self, mock_client_class):
        """Test successful Mastodon DM send."""
        from tests.factories import NotificationChannelFactory

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"id": "123"}
        mock_client.post.return_value = mock_response

        mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

        channel = NotificationChannelFactory(
            platform="mastodon",
            handle="@user@mastodon.social",
        )
        sender = MastodonSender()

        result = sender.send(channel, "Test notification message")

        assert result is True

    @patch("notifications.senders.mastodon.httpx.Client")
    def test_send_failure(self, mock_client_class):
        """Test failed Mastodon DM send."""
        from tests.factories import NotificationChannelFactory

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_client.post.return_value = mock_response

        mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

        channel = NotificationChannelFactory(
            platform="mastodon",
            handle="@user@mastodon.social",
        )
        sender = MastodonSender()

        result = sender.send(channel, "Test notification message")

        assert result is False
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/notifications/test_mastodon_sender.py -v`
Expected: FAIL with "ModuleNotFoundError"

**Step 3: Implement Mastodon sender**

Create `notifications/senders/mastodon.py`:

```python
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
```

**Step 4: Update __init__.py**

```python
from .base import NotificationSender
from .bluesky import BlueskySender
from .discord import DiscordSender
from .mastodon import MastodonSender
from .slack import SlackSender

__all__ = [
    "NotificationSender",
    "BlueskySender",
    "DiscordSender",
    "MastodonSender",
    "SlackSender",
]
```

**Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/notifications/test_mastodon_sender.py -v`
Expected: All tests PASS

**Step 6: Commit**

```bash
git add notifications/senders/ tests/notifications/test_mastodon_sender.py
git commit -m "feat(notifications): add Mastodon sender

- Handle validation (@user@instance format)
- DM via direct visibility status
- Handle parsing for different instances"
```

---

## Task 9: Create Sender Registry

**Files:**
- Create: `notifications/senders/registry.py`
- Modify: `notifications/senders/__init__.py`
- Create: `tests/notifications/test_registry.py`

**Step 1: Write failing test**

Create `tests/notifications/test_registry.py`:

```python
import pytest

from notifications.senders.registry import get_sender, SenderRegistry


class TestSenderRegistry:
    def test_get_discord_sender(self):
        """Test getting Discord sender."""
        from notifications.senders.discord import DiscordSender

        sender = get_sender("discord")
        assert isinstance(sender, DiscordSender)

    def test_get_slack_sender(self):
        """Test getting Slack sender."""
        from notifications.senders.slack import SlackSender

        sender = get_sender("slack")
        assert isinstance(sender, SlackSender)

    def test_get_bluesky_sender(self):
        """Test getting Bluesky sender."""
        from notifications.senders.bluesky import BlueskySender

        sender = get_sender("bluesky")
        assert isinstance(sender, BlueskySender)

    def test_get_mastodon_sender(self):
        """Test getting Mastodon sender."""
        from notifications.senders.mastodon import MastodonSender

        sender = get_sender("mastodon")
        assert isinstance(sender, MastodonSender)

    def test_get_unknown_sender_returns_none(self):
        """Test getting unknown sender returns None."""
        sender = get_sender("unknown_platform")
        assert sender is None

    def test_senders_are_cached(self):
        """Test that sender instances are cached."""
        sender1 = get_sender("discord")
        sender2 = get_sender("discord")
        assert sender1 is sender2
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/notifications/test_registry.py -v`
Expected: FAIL with "ModuleNotFoundError"

**Step 3: Implement registry**

Create `notifications/senders/registry.py`:

```python
from typing import Optional

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
    def get(cls, platform: str) -> Optional[NotificationSender]:
        """Get sender instance for platform. Returns None if not found."""
        if platform not in cls.SENDER_MAP:
            return None

        if platform not in cls._instances:
            cls._instances[platform] = cls.SENDER_MAP[platform]()

        return cls._instances[platform]


def get_sender(platform: str) -> Optional[NotificationSender]:
    """Convenience function to get sender for platform."""
    return SenderRegistry.get(platform)
```

**Step 4: Update __init__.py**

```python
from .base import NotificationSender
from .bluesky import BlueskySender
from .discord import DiscordSender
from .mastodon import MastodonSender
from .registry import SenderRegistry, get_sender
from .slack import SlackSender

__all__ = [
    "NotificationSender",
    "BlueskySender",
    "DiscordSender",
    "MastodonSender",
    "SlackSender",
    "SenderRegistry",
    "get_sender",
]
```

**Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/notifications/test_registry.py -v`
Expected: All tests PASS

**Step 6: Commit**

```bash
git add notifications/senders/ tests/notifications/test_registry.py
git commit -m "feat(notifications): add sender registry

- Central registry for all sender implementations
- get_sender() convenience function
- Singleton pattern for sender instances"
```

---

## Task 10: Create Notification Dispatcher Service

**Files:**
- Create: `notifications/services.py`
- Create: `tests/notifications/test_services.py`

**Step 1: Write failing test**

Create `tests/notifications/test_services.py`:

```python
import pytest
from unittest.mock import patch, MagicMock

from notifications.services import dispatch_notification, dispatch_to_all_channels


@pytest.mark.django_db
class TestDispatchNotification:
    @patch("notifications.senders.discord.DiscordSender.send")
    def test_dispatch_to_single_channel_success(self, mock_send):
        """Test dispatching to a single channel successfully."""
        from tests.factories import NotificationChannelFactory

        mock_send.return_value = True
        channel = NotificationChannelFactory(platform="discord")

        result = dispatch_notification(channel, "Test message")

        assert result is True
        mock_send.assert_called_once()
        channel.refresh_from_db()
        assert channel.failure_count == 0

    @patch("notifications.senders.discord.DiscordSender.send")
    def test_dispatch_failure_increments_count(self, mock_send):
        """Test that failed dispatch increments failure count."""
        from tests.factories import NotificationChannelFactory

        mock_send.return_value = False
        channel = NotificationChannelFactory(platform="discord", failure_count=0)

        result = dispatch_notification(channel, "Test message")

        assert result is False
        channel.refresh_from_db()
        assert channel.failure_count == 1

    @patch("notifications.senders.discord.DiscordSender.send")
    def test_dispatch_to_disabled_channel_skipped(self, mock_send):
        """Test that disabled channels are skipped."""
        from tests.factories import NotificationChannelFactory

        channel = NotificationChannelFactory(platform="discord", is_enabled=False)

        result = dispatch_notification(channel, "Test message")

        assert result is False
        mock_send.assert_not_called()


@pytest.mark.django_db
class TestDispatchToAllChannels:
    @patch("notifications.senders.discord.DiscordSender.send")
    @patch("notifications.senders.slack.SlackSender.send")
    def test_dispatch_to_multiple_channels(self, mock_slack, mock_discord):
        """Test dispatching to multiple channels."""
        from tests.factories import NotificationChannelFactory, SavedSearchFactory

        mock_discord.return_value = True
        mock_slack.return_value = True

        saved_search = SavedSearchFactory()
        NotificationChannelFactory(
            user=saved_search.user,
            platform="discord",
            is_enabled=True,
        )
        NotificationChannelFactory(
            user=saved_search.user,
            platform="slack",
            is_enabled=True,
        )

        results = dispatch_to_all_channels(saved_search, "Test message")

        assert len(results) == 2
        assert all(r["success"] for r in results)

    @patch("notifications.senders.discord.DiscordSender.send")
    def test_dispatch_respects_channel_override(self, mock_discord):
        """Test that channel overrides are respected."""
        from tests.factories import NotificationChannelFactory, SavedSearchFactory

        mock_discord.return_value = True

        saved_search = SavedSearchFactory()
        saved_search.notification_channels = {"channels": ["discord"]}
        saved_search.save()

        NotificationChannelFactory(
            user=saved_search.user,
            platform="discord",
            is_enabled=True,
        )
        NotificationChannelFactory(
            user=saved_search.user,
            platform="slack",
            is_enabled=True,
        )

        results = dispatch_to_all_channels(saved_search, "Test message")

        assert len(results) == 1
        assert results[0]["platform"] == "discord"

    def test_dispatch_with_no_channels(self):
        """Test dispatching when user has no channels configured."""
        from tests.factories import SavedSearchFactory

        saved_search = SavedSearchFactory()

        results = dispatch_to_all_channels(saved_search, "Test message")

        assert len(results) == 0
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/notifications/test_services.py -v`
Expected: FAIL with "ModuleNotFoundError"

**Step 3: Implement services**

Create `notifications/services.py`:

```python
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
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/notifications/test_services.py -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add notifications/services.py tests/notifications/test_services.py
git commit -m "feat(notifications): add notification dispatcher service

- dispatch_notification() for single channel
- dispatch_to_all_channels() for saved search
- Automatic failure/success tracking"
```

---

## Task 11: Integrate with Existing Notification Flow

**Files:**
- Modify: `searches/tasks.py`
- Create: `tests/notifications/test_integration.py`

**Step 1: Write failing test**

Create `tests/notifications/test_integration.py`:

```python
import pytest
from unittest.mock import patch
from django.core import mail


@pytest.mark.django_db
class TestNotificationIntegration:
    @patch("notifications.senders.discord.DiscordSender.send")
    def test_immediate_notification_sends_to_channels(self, mock_discord):
        """Test immediate notifications are sent to configured channels."""
        from tests.factories import (
            MeetingDocumentFactory,
            MeetingPageFactory,
            NotificationChannelFactory,
            SavedSearchFactory,
            SearchFactory,
        )
        from searches.tasks import check_saved_search_for_updates

        mock_discord.return_value = True

        # Create user with Discord channel
        search = SearchFactory(search_term="budget")
        saved_search = SavedSearchFactory(
            search=search,
            notification_frequency="immediate",
        )
        NotificationChannelFactory(
            user=saved_search.user,
            platform="discord",
            is_enabled=True,
        )

        # Create matching page
        doc = MeetingDocumentFactory()
        search.municipalities.add(doc.municipality)
        MeetingPageFactory(
            document=doc,
            text="Budget discussion for 2025.",
        )

        # Trigger check
        check_saved_search_for_updates(saved_search.id)

        # Email should still be sent (fallback)
        assert len(mail.outbox) == 1

        # Discord should also be called
        mock_discord.assert_called_once()

    @patch("notifications.senders.discord.DiscordSender.send")
    def test_channel_failure_falls_back_to_email(self, mock_discord):
        """Test that channel failure still sends email."""
        from tests.factories import (
            MeetingDocumentFactory,
            MeetingPageFactory,
            NotificationChannelFactory,
            SavedSearchFactory,
            SearchFactory,
        )
        from searches.tasks import check_saved_search_for_updates

        mock_discord.return_value = False  # Discord fails

        search = SearchFactory(search_term="budget")
        saved_search = SavedSearchFactory(
            search=search,
            notification_frequency="immediate",
        )
        NotificationChannelFactory(
            user=saved_search.user,
            platform="discord",
            is_enabled=True,
        )

        doc = MeetingDocumentFactory()
        search.municipalities.add(doc.municipality)
        MeetingPageFactory(
            document=doc,
            text="Budget discussion for 2025.",
        )

        check_saved_search_for_updates(saved_search.id)

        # Email should still be sent
        assert len(mail.outbox) == 1

    def test_no_channels_configured_sends_email_only(self):
        """Test that notification works with email only when no channels."""
        from tests.factories import (
            MeetingDocumentFactory,
            MeetingPageFactory,
            SavedSearchFactory,
            SearchFactory,
        )
        from searches.tasks import check_saved_search_for_updates

        search = SearchFactory(search_term="budget")
        saved_search = SavedSearchFactory(
            search=search,
            notification_frequency="immediate",
        )

        doc = MeetingDocumentFactory()
        search.municipalities.add(doc.municipality)
        MeetingPageFactory(
            document=doc,
            text="Budget discussion for 2025.",
        )

        check_saved_search_for_updates(saved_search.id)

        # Email sent
        assert len(mail.outbox) == 1
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/notifications/test_integration.py -v`
Expected: FAIL (integration not implemented yet)

**Step 3: Modify searches/tasks.py to integrate channels**

Modify `searches/tasks.py`, update `check_saved_search_for_updates`:

```python
def check_saved_search_for_updates(saved_search_id) -> dict[str, str | int]:
    """
    Check a single saved search for new results and send notification if needed.

    Args:
        saved_search_id: ID of the SavedSearch to check

    Returns:
        Dict with status information
    """
    try:
        saved_search = SavedSearch.objects.select_related("search", "user").get(
            id=saved_search_id
        )
    except SavedSearch.DoesNotExist:
        logger.error(f"SavedSearch {saved_search_id} not found")
        return {
            "status": "not_found",
            "saved_search_id": str(saved_search_id),
            "action": "SavedSearch not found in database",
        }

    # Get new pages for this search
    new_pages = saved_search.search.update_search()

    # If no new results, nothing to do
    if not new_pages.exists():
        logger.debug(
            f"No new results for SavedSearch {saved_search.id} ({saved_search.name})"
        )
        return {
            "status": "no_new_results",
            "saved_search_id": str(saved_search.id),
            "new_results_count": 0,
            "action": "No new results found",
        }

    new_results_count = new_pages.count()
    logger.info(
        f"Found {new_results_count} new results for SavedSearch {saved_search.id} ({saved_search.name})"
    )

    # Handle based on notification frequency
    if saved_search.notification_frequency == "immediate":
        # Send to additional notification channels
        _send_to_notification_channels(saved_search, new_pages)

        # Send email notification (always - fallback)
        saved_search.send_search_notification(new_pages=new_pages)
        logger.info(
            f"Sent immediate notification for SavedSearch {saved_search.id} to {saved_search.user.email}"
        )
        return {
            "status": "notified",
            "saved_search_id": str(saved_search.id),
            "new_results_count": new_results_count,
            "action": f"Sent immediate notification to {saved_search.user.email}",
        }
    else:
        # Flag for digest notification
        saved_search.has_pending_results = True
        saved_search.last_checked = timezone.now()
        saved_search.save(update_fields=["has_pending_results", "last_checked"])
        logger.info(
            f"Flagged SavedSearch {saved_search.id} for {saved_search.notification_frequency} digest"
        )
        return {
            "status": "pending",
            "saved_search_id": str(saved_search.id),
            "new_results_count": new_results_count,
            "action": f"Marked for {saved_search.notification_frequency} digest",
        }


def _send_to_notification_channels(saved_search, new_pages) -> None:
    """
    Send notification to user's configured notification channels.

    Args:
        saved_search: The SavedSearch that matched
        new_pages: QuerySet of new MeetingPage objects
    """
    from notifications.services import dispatch_to_all_channels

    # Format message for non-email channels
    message = _format_channel_message(saved_search, new_pages)

    # Dispatch to all configured channels
    results = dispatch_to_all_channels(saved_search, message)

    for result in results:
        if result["success"]:
            logger.info(
                f"Sent {result['platform']} notification for SavedSearch {saved_search.id}"
            )
        else:
            logger.warning(
                f"Failed to send {result['platform']} notification for SavedSearch {saved_search.id}"
            )


def _format_channel_message(saved_search, new_pages) -> str:
    """Format notification message for non-email channels."""
    count = new_pages.count()
    search_name = saved_search.name

    if count == 1:
        page = new_pages.first()
        return (
            f'🔔 New result for "{search_name}"\n\n'
            f"Meeting: {page.document.meeting_name}\n"
            f"Date: {page.document.meeting_date}\n"
            f"Page {page.page_number}\n\n"
            f"View on Civic Observer: https://civic.observer/searches/"
        )
    else:
        return (
            f'🔔 {count} new results for "{search_name}"\n\n'
            f"View on Civic Observer: https://civic.observer/searches/"
        )
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/notifications/test_integration.py -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add searches/tasks.py tests/notifications/test_integration.py
git commit -m "feat(notifications): integrate channels with search notification flow

- Send to notification channels on immediate notifications
- Email always sent as fallback
- Format messages for non-email channels"
```

---

## Task 12: Add User-Facing Channel Management Views

**Files:**
- Create: `notifications/views.py`
- Create: `notifications/urls.py`
- Create: `notifications/forms.py`
- Modify: `config/urls.py`
- Create: `templates/notifications/channel_list.html`
- Create: `templates/notifications/partials/channel_form.html`
- Create: `tests/notifications/test_views.py`

**Step 1: Write failing test**

Create `tests/notifications/test_views.py`:

```python
import pytest
from django.urls import reverse


@pytest.mark.django_db
class TestChannelListView:
    def test_requires_login(self, client):
        """Test view requires authentication."""
        url = reverse("notifications:channel-list")
        response = client.get(url)

        assert response.status_code == 302

    def test_shows_user_channels(self, client):
        """Test view shows user's channels."""
        from tests.factories import NotificationChannelFactory, UserFactory

        user = UserFactory()
        NotificationChannelFactory(user=user, platform="discord")

        client.force_login(user)
        url = reverse("notifications:channel-list")
        response = client.get(url)

        assert response.status_code == 200
        assert b"discord" in response.content.lower()


@pytest.mark.django_db
class TestChannelCreateView:
    def test_can_create_channel(self, client):
        """Test creating a new channel."""
        from tests.factories import UserFactory

        user = UserFactory()
        client.force_login(user)

        url = reverse("notifications:channel-create")
        response = client.post(
            url,
            {
                "platform": "slack",
                "handle": "https://hooks.slack.com/services/T00/B00/xxx",
            },
        )

        assert response.status_code in [200, 302]

    def test_validates_handle_format(self, client):
        """Test that handle is validated."""
        from tests.factories import UserFactory

        user = UserFactory()
        client.force_login(user)

        url = reverse("notifications:channel-create")
        response = client.post(
            url,
            {
                "platform": "slack",
                "handle": "not-a-valid-webhook",
            },
        )

        # Should show validation error
        assert response.status_code == 200
        assert (
            b"error" in response.content.lower()
            or b"invalid" in response.content.lower()
        )


@pytest.mark.django_db
class TestChannelDeleteView:
    def test_can_delete_own_channel(self, client):
        """Test deleting own channel."""
        from notifications.models import NotificationChannel
        from tests.factories import NotificationChannelFactory, UserFactory

        user = UserFactory()
        channel = NotificationChannelFactory(user=user)

        client.force_login(user)
        url = reverse("notifications:channel-delete", args=[channel.pk])
        response = client.post(url)

        assert response.status_code in [200, 302]
        assert not NotificationChannel.objects.filter(pk=channel.pk).exists()

    def test_cannot_delete_other_users_channel(self, client):
        """Test cannot delete another user's channel."""
        from notifications.models import NotificationChannel
        from tests.factories import NotificationChannelFactory, UserFactory

        user = UserFactory()
        other_user = UserFactory()
        channel = NotificationChannelFactory(user=other_user)

        client.force_login(user)
        url = reverse("notifications:channel-delete", args=[channel.pk])
        response = client.post(url)

        assert response.status_code == 404
        assert NotificationChannel.objects.filter(pk=channel.pk).exists()
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/notifications/test_views.py -v`
Expected: FAIL with "NoReverseMatch" or similar

**Step 3: Create forms**

Create `notifications/forms.py`:

```python
from django import forms

from notifications.senders import get_sender

from .models import NotificationChannel


class NotificationChannelForm(forms.ModelForm):
    """Form for creating/editing notification channels."""

    class Meta:
        model = NotificationChannel
        fields = ["platform", "handle"]
        widgets = {
            "platform": forms.Select(
                attrs={
                    "class": "mt-1 block w-full rounded-md border-gray-300 shadow-sm"
                }
            ),
            "handle": forms.TextInput(
                attrs={
                    "class": "mt-1 block w-full rounded-md border-gray-300 shadow-sm",
                    "placeholder": "Enter username or webhook URL",
                }
            ),
        }

    def clean(self):
        cleaned_data = super().clean()
        platform = cleaned_data.get("platform")
        handle = cleaned_data.get("handle")

        if platform and handle:
            sender = get_sender(platform)
            if sender and not sender.validate_handle(handle):
                self.add_error(
                    "handle",
                    f"Invalid format for {platform}. Please check the format.",
                )

        return cleaned_data
```

**Step 4: Create views**

Create `notifications/views.py`:

```python
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from django.urls import reverse_lazy
from django.views.generic import CreateView, DeleteView, ListView

from .forms import NotificationChannelForm
from .models import NotificationChannel


class ChannelListView(LoginRequiredMixin, ListView):
    """List user's notification channels."""

    model = NotificationChannel
    template_name = "notifications/channel_list.html"
    context_object_name = "channels"

    def get_queryset(self):
        return NotificationChannel.objects.filter(user=self.request.user)


class ChannelCreateView(LoginRequiredMixin, CreateView):
    """Create a new notification channel."""

    model = NotificationChannel
    form_class = NotificationChannelForm
    template_name = "notifications/partials/channel_form.html"
    success_url = reverse_lazy("notifications:channel-list")

    def form_valid(self, form):
        form.instance.user = self.request.user
        self.object = form.save()

        # Return HTMX response
        if self.request.headers.get("HX-Request"):
            html = render_to_string(
                "notifications/partials/channel_row.html",
                {"channel": self.object},
                request=self.request,
            )
            return HttpResponse(html)

        return super().form_valid(form)

    def form_invalid(self, form):
        if self.request.headers.get("HX-Request"):
            html = render_to_string(
                "notifications/partials/channel_form.html",
                {"form": form},
                request=self.request,
            )
            return HttpResponse(html)
        return super().form_invalid(form)


class ChannelDeleteView(LoginRequiredMixin, DeleteView):
    """Delete a notification channel."""

    model = NotificationChannel
    success_url = reverse_lazy("notifications:channel-list")

    def get_queryset(self):
        return NotificationChannel.objects.filter(user=self.request.user)

    def delete(self, request, *args, **kwargs):
        self.object = self.get_object()
        self.object.delete()

        if request.headers.get("HX-Request"):
            return HttpResponse("")

        return super().delete(request, *args, **kwargs)
```

**Step 5: Create URLs**

Create `notifications/urls.py`:

```python
from django.urls import path

from . import views

app_name = "notifications"

urlpatterns = [
    path("channels/", views.ChannelListView.as_view(), name="channel-list"),
    path("channels/create/", views.ChannelCreateView.as_view(), name="channel-create"),
    path(
        "channels/<uuid:pk>/delete/",
        views.ChannelDeleteView.as_view(),
        name="channel-delete",
    ),
]
```

**Step 6: Add to main URLs**

Modify `config/urls.py`, add:

```python
path("notifications/", include("notifications.urls")),
```

**Step 7: Create templates**

Create `templates/notifications/channel_list.html`:

```html
{% extends "base.html" %}

{% block content %}
<div class="max-w-4xl mx-auto py-8 px-4">
    <div class="flex justify-between items-center mb-6">
        <h1 class="text-2xl font-bold text-gray-900">Notification Channels</h1>
        <button hx-get="{% url 'notifications:channel-create' %}"
                hx-target="#channel-form-container"
                hx-swap="innerHTML"
                class="inline-flex items-center px-4 py-2 border border-transparent text-sm font-medium rounded-md shadow-sm text-white bg-indigo-600 hover:bg-indigo-700">
            Add Channel
        </button>
    </div>

    <p class="text-gray-600 mb-6">
        Configure additional notification channels beyond email. When a saved search finds new results,
        notifications will be sent to all enabled channels.
    </p>

    <div id="channel-form-container" class="mb-6"></div>

    <div id="channel-list" class="bg-white shadow overflow-hidden sm:rounded-md">
        {% if channels %}
        <ul class="divide-y divide-gray-200">
            {% for channel in channels %}
            {% include "notifications/partials/channel_row.html" %}
            {% endfor %}
        </ul>
        {% else %}
        <div class="p-6 text-center text-gray-500">
            <p>No notification channels configured yet.</p>
            <p class="text-sm mt-2">Add a channel to receive notifications on Discord, Slack, Bluesky, or Mastodon.</p>
        </div>
        {% endif %}
    </div>
</div>
{% endblock %}
```

Create `templates/notifications/partials/channel_form.html`:

```html
<form hx-post="{% url 'notifications:channel-create' %}"
      hx-target="#channel-list"
      hx-swap="afterbegin"
      class="bg-gray-50 p-4 rounded-lg mb-4">
    {% csrf_token %}

    <div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div>
            <label for="id_platform" class="block text-sm font-medium text-gray-700">Platform</label>
            {{ form.platform }}
            {% if form.platform.errors %}
            <p class="mt-1 text-sm text-red-600">{{ form.platform.errors.0 }}</p>
            {% endif %}
        </div>

        <div>
            <label for="id_handle" class="block text-sm font-medium text-gray-700">Handle / Webhook URL</label>
            {{ form.handle }}
            {% if form.handle.errors %}
            <p class="mt-1 text-sm text-red-600">{{ form.handle.errors.0 }}</p>
            {% endif %}
        </div>
    </div>

    {% if form.non_field_errors %}
    <div class="mt-2 text-sm text-red-600">
        {{ form.non_field_errors.0 }}
    </div>
    {% endif %}

    <div class="mt-4 flex justify-end gap-2">
        <button type="button"
                hx-get="{% url 'notifications:channel-list' %}"
                hx-target="#channel-form-container"
                hx-swap="innerHTML"
                class="px-4 py-2 border border-gray-300 rounded-md text-sm font-medium text-gray-700 hover:bg-gray-50">
            Cancel
        </button>
        <button type="submit"
                class="px-4 py-2 border border-transparent rounded-md shadow-sm text-sm font-medium text-white bg-indigo-600 hover:bg-indigo-700">
            Save Channel
        </button>
    </div>
</form>
```

Create `templates/notifications/partials/channel_row.html`:

```html
<li id="channel-{{ channel.pk }}" class="px-4 py-4 sm:px-6">
    <div class="flex items-center justify-between">
        <div class="flex items-center">
            <div class="flex-shrink-0">
                {% if channel.platform == "discord" %}
                <span class="text-2xl">💬</span>
                {% elif channel.platform == "slack" %}
                <span class="text-2xl">📱</span>
                {% elif channel.platform == "bluesky" %}
                <span class="text-2xl">🦋</span>
                {% elif channel.platform == "mastodon" %}
                <span class="text-2xl">🐘</span>
                {% endif %}
            </div>
            <div class="ml-4">
                <p class="text-sm font-medium text-gray-900">{{ channel.get_platform_display }}</p>
                <p class="text-sm text-gray-500">{{ channel.handle|truncatechars:50 }}</p>
            </div>
        </div>
        <div class="flex items-center gap-4">
            {% if channel.is_enabled %}
            <span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-800">
                Enabled
            </span>
            {% else %}
            <span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-red-100 text-red-800">
                Disabled
            </span>
            {% endif %}
            <button hx-post="{% url 'notifications:channel-delete' channel.pk %}"
                    hx-target="#channel-{{ channel.pk }}"
                    hx-swap="outerHTML"
                    hx-confirm="Are you sure you want to delete this channel?"
                    class="text-red-600 hover:text-red-900 text-sm">
                Delete
            </button>
        </div>
    </div>
</li>
```

**Step 8: Run tests to verify they pass**

Run: `uv run pytest tests/notifications/test_views.py -v`
Expected: All tests PASS

**Step 9: Commit**

```bash
git add notifications/views.py notifications/urls.py notifications/forms.py config/urls.py templates/notifications/
git commit -m "feat(notifications): add user-facing channel management views

- Channel list view showing all configured channels
- HTMX-powered create form with handle validation
- Delete channel with confirmation
- Templates with Tailwind styling"
```

---

## Task 13: Add Navigation Link

**Files:**
- Modify: `templates/base.html`

**Step 1: Add link to navigation**

Modify `templates/base.html`, add link in user navigation section:

```html
<a href="{% url 'notifications:channel-list' %}"
   class="{% if request.resolver_match.url_name == 'channel-list' %}text-indigo-600{% else %}text-gray-600 hover:text-gray-900{% endif %}">
    Notifications
</a>
```

**Step 2: Verify manually**

Start dev server and verify link appears and works.

**Step 3: Commit**

```bash
git add templates/base.html
git commit -m "feat(notifications): add navigation link to channel settings"
```

---

## Task 14: Add Settings Configuration

**Files:**
- Modify: `config/settings/base.py`
- Modify: `.env-dist`

**Step 1: Add settings**

Modify `config/settings/base.py`:

```python
# Notification Channel Settings
DISCORD_BOT_TOKEN = env.str("DISCORD_BOT_TOKEN", "")
BLUESKY_BOT_HANDLE = env.str("BLUESKY_BOT_HANDLE", "")
BLUESKY_BOT_PASSWORD = env.str("BLUESKY_BOT_PASSWORD", "")
MASTODON_ACCESS_TOKEN = env.str("MASTODON_ACCESS_TOKEN", "")
MASTODON_INSTANCE_URL = env.str("MASTODON_INSTANCE_URL", "https://mastodon.social")
```

**Step 2: Update .env-dist**

Add to `.env-dist`:

```bash
# Notification Channels (optional)
DISCORD_BOT_TOKEN=
BLUESKY_BOT_HANDLE=
BLUESKY_BOT_PASSWORD=
MASTODON_ACCESS_TOKEN=
MASTODON_INSTANCE_URL=https://mastodon.social
```

**Step 3: Commit**

```bash
git add config/settings/base.py .env-dist
git commit -m "chore: add notification channel environment variables"
```

---

## Task 15: Add Channel Disabled Email Notification

**Files:**
- Modify: `notifications/models.py`
- Create: `templates/email/channel_disabled.txt`
- Create: `templates/email/channel_disabled.html`
- Create: `tests/notifications/test_channel_disabled_email.py`

**Step 1: Write failing test**

Create `tests/notifications/test_channel_disabled_email.py`:

```python
import pytest
from django.core import mail


@pytest.mark.django_db
class TestChannelDisabledEmail:
    def test_email_sent_when_channel_disabled(self):
        """Test email is sent when channel gets disabled due to failures."""
        from tests.factories import NotificationChannelFactory

        channel = NotificationChannelFactory(failure_count=2, is_enabled=True)

        # Third failure should trigger email
        channel.record_failure()

        channel.refresh_from_db()
        assert channel.is_enabled is False
        assert len(mail.outbox) == 1
        assert "disabled" in mail.outbox[0].subject.lower()
        assert channel.platform in mail.outbox[0].body

    def test_no_email_before_max_failures(self):
        """Test no email sent before reaching max failures."""
        from tests.factories import NotificationChannelFactory

        channel = NotificationChannelFactory(failure_count=0, is_enabled=True)

        channel.record_failure()

        assert len(mail.outbox) == 0
        assert channel.is_enabled is True
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/notifications/test_channel_disabled_email.py -v`
Expected: FAIL (email not sent)

**Step 3: Update model to send email**

Modify `notifications/models.py`:

```python
def record_failure(self) -> None:
    """Record a delivery failure. Disables channel after MAX_FAILURES and notifies user."""
    self.failure_count += 1
    was_enabled = self.is_enabled

    if self.failure_count >= self.MAX_FAILURES:
        self.is_enabled = False

    self.save(update_fields=["failure_count", "is_enabled"])

    # Send email notification if channel was just disabled
    if was_enabled and not self.is_enabled:
        self._send_disabled_notification()


def _send_disabled_notification(self) -> None:
    """Send email to user that their notification channel was disabled."""
    from django.core.mail import EmailMultiAlternatives
    from django.template.loader import get_template, render_to_string

    context = {"channel": self}
    txt_content = render_to_string("email/channel_disabled.txt", context=context)
    html_content = get_template("email/channel_disabled.html").render(context=context)

    msg = EmailMultiAlternatives(
        subject=f"Your {self.get_platform_display()} notification channel was disabled",
        to=[self.user.email],
        from_email="Civic Observer <noreply@civic.observer>",
        body=txt_content,
    )
    msg.attach_alternative(html_content, "text/html")
    msg.send()
```

**Step 4: Create email templates**

Create `templates/email/channel_disabled.txt`:

```
Hello,

Your {{ channel.get_platform_display }} notification channel has been disabled due to repeated delivery failures.

Channel: {{ channel.get_platform_display }}
Handle: {{ channel.handle }}

This usually happens when:
- Your username/handle has changed
- The webhook URL is no longer valid
- Privacy settings prevent receiving DMs

To continue receiving notifications on {{ channel.get_platform_display }}, please:
1. Visit https://civic.observer/notifications/channels/
2. Delete the disabled channel
3. Add a new channel with correct information

You will continue to receive email notifications for your saved searches.

Best regards,
The Civic Observer Team
```

Create `templates/email/channel_disabled.html`:

```html
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
</head>
<body style="font-family: sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
    <h2>Notification Channel Disabled</h2>

    <p>Your <strong>{{ channel.get_platform_display }}</strong> notification channel has been disabled due to repeated delivery failures.</p>

    <div style="background: #f3f4f6; padding: 15px; border-radius: 8px; margin: 20px 0;">
        <p style="margin: 0;"><strong>Channel:</strong> {{ channel.get_platform_display }}</p>
        <p style="margin: 10px 0 0 0;"><strong>Handle:</strong> {{ channel.handle }}</p>
    </div>

    <p>This usually happens when:</p>
    <ul>
        <li>Your username/handle has changed</li>
        <li>The webhook URL is no longer valid</li>
        <li>Privacy settings prevent receiving DMs</li>
    </ul>

    <p>To continue receiving notifications on {{ channel.get_platform_display }}:</p>
    <ol>
        <li>Visit <a href="https://civic.observer/notifications/channels/">your notification settings</a></li>
        <li>Delete the disabled channel</li>
        <li>Add a new channel with correct information</li>
    </ol>

    <p>You will continue to receive email notifications for your saved searches.</p>

    <p style="color: #6b7280; margin-top: 30px;">
        Best regards,<br>
        The Civic Observer Team
    </p>
</body>
</html>
```

**Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/notifications/test_channel_disabled_email.py -v`
Expected: All tests PASS

**Step 6: Commit**

```bash
git add notifications/models.py templates/email/channel_disabled.*
git commit -m "feat(notifications): send email when channel is auto-disabled

- Email notification when channel disabled after 3 failures
- Both HTML and plain text templates
- Instructions for re-enabling"
```

---

## Task 16: Run Full Test Suite and Lint

**Step 1: Run all tests**

Run: `uv run pytest --cov`
Expected: All tests PASS, coverage acceptable

**Step 2: Run linting**

Run: `uv run --group dev ruff check .`
Expected: No errors

**Step 3: Run type checking**

Run: `uv run --group dev mypy .`
Expected: No errors (or only pre-existing ones)

**Step 4: Commit any fixes**

```bash
git add -A
git commit -m "fix: address linting and type checking issues"
```

---

## Task 17: Create PR

**Step 1: Push branch**

```bash
git push -u origin feature/notification-channels
```

**Step 2: Create PR**

```bash
gh pr create --title "feat: add multi-platform notification channels" --body "$(cat <<'EOF'
## Summary
- Add support for Discord, Slack, Bluesky, and Mastodon notifications
- Users can configure notification channels in settings
- Per-search channel overrides supported
- Email always sent as fallback
- Auto-disable after 3 consecutive failures with email notification

## Test plan
- [ ] Run full test suite
- [ ] Test adding each channel type manually
- [ ] Test notification delivery to each platform
- [ ] Test channel auto-disable behavior
- [ ] Verify email fallback works

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

Plan complete and saved to `docs/plans/2025-11-29-notification-channels.md`. Two execution options:

**1. Subagent-Driven (this session)** - I dispatch fresh subagent per task, review between tasks, fast iteration

**2. Parallel Session (separate)** - Open new session with executing-plans, batch execution with checkpoints

Which approach?
