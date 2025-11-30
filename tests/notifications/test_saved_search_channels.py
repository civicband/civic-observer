"""Tests for SavedSearch notification channels functionality."""

import pytest

from tests.factories import NotificationChannelFactory, SavedSearchFactory


@pytest.mark.django_db
class TestSavedSearchNotificationChannels:
    """Test notification_channels field and get_effective_channels() method."""

    def test_default_notification_channels_is_empty_dict(self):
        """Test notification_channels defaults to empty dict."""
        saved_search = SavedSearchFactory()

        assert saved_search.notification_channels == {}

    def test_can_set_channel_overrides(self):
        """Test can set channel overrides."""
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
