from unittest.mock import MagicMock, patch

import pytest

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
    @patch("notifications.senders.bluesky.IdResolver")
    @patch("notifications.senders.bluesky.Client")
    def test_send_success(self, mock_client_class, mock_resolver_class):
        """Test successful Bluesky DM send."""
        from tests.factories import NotificationChannelFactory

        # Mock the client
        mock_client = MagicMock()
        mock_client.me.did = "did:plc:sender"
        mock_client_class.return_value = mock_client

        # Mock the DM client
        mock_dm_client = MagicMock()
        mock_client.with_bsky_chat_proxy.return_value = mock_dm_client

        # Mock conversation response
        mock_convo = MagicMock()
        mock_convo.convo.id = "convo123"
        mock_dm_client.chat.bsky.convo.get_convo_for_members.return_value = mock_convo

        # Mock resolver
        mock_resolver = MagicMock()
        mock_resolver.handle.resolve.return_value = "did:plc:recipient"
        mock_resolver_class.return_value = mock_resolver

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
        mock_client.login.assert_called_once_with("bot.bsky.social", "password")
        mock_resolver.handle.resolve.assert_called_once_with("recipient.bsky.social")
        mock_dm_client.chat.bsky.convo.send_message.assert_called_once()

    @patch("notifications.senders.bluesky.IdResolver")
    @patch("notifications.senders.bluesky.Client")
    def test_send_failure_no_credentials(self, mock_client_class, mock_resolver_class):
        """Test send fails without credentials."""
        from tests.factories import NotificationChannelFactory

        channel = NotificationChannelFactory(
            platform="bluesky",
            handle="recipient.bsky.social",
        )
        sender = BlueskySender()

        with patch.object(sender, "_get_credentials", return_value=("", "")):
            result = sender.send(channel, "Test notification message")

        assert result is False
        mock_client_class.assert_not_called()

    @patch("notifications.senders.bluesky.IdResolver")
    @patch("notifications.senders.bluesky.Client")
    def test_send_failure_resolve_error(self, mock_client_class, mock_resolver_class):
        """Test send fails when handle resolution fails."""
        from tests.factories import NotificationChannelFactory

        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        # Mock resolver to return None (resolution failed)
        mock_resolver = MagicMock()
        mock_resolver.handle.resolve.return_value = None
        mock_resolver_class.return_value = mock_resolver

        channel = NotificationChannelFactory(
            platform="bluesky",
            handle="nonexistent.bsky.social",
        )
        sender = BlueskySender()

        with patch.object(
            sender, "_get_credentials", return_value=("bot.bsky.social", "password")
        ):
            result = sender.send(channel, "Test notification message")

        assert result is False
