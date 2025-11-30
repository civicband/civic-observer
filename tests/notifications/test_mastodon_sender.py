from unittest.mock import MagicMock, patch

import pytest

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
    @patch("notifications.senders.mastodon.settings")
    def test_send_success(self, mock_settings, mock_client_class):
        """Test successful Mastodon DM send."""
        from tests.factories import NotificationChannelFactory

        mock_settings.MASTODON_ACCESS_TOKEN = "test_token"
        mock_settings.MASTODON_INSTANCE_URL = "https://mastodon.social"

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
    @patch("notifications.senders.mastodon.settings")
    def test_send_failure(self, mock_settings, mock_client_class):
        """Test failed Mastodon DM send."""
        from tests.factories import NotificationChannelFactory

        mock_settings.MASTODON_ACCESS_TOKEN = "test_token"
        mock_settings.MASTODON_INSTANCE_URL = "https://mastodon.social"

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
