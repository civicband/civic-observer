from unittest.mock import patch

import pytest

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
