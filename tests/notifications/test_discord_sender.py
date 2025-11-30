from unittest.mock import MagicMock, patch

import pytest
from django.test import override_settings

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
    @override_settings(DISCORD_BOT_TOKEN="test_token")
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
