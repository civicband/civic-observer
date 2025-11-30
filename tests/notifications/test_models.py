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
