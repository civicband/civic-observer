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
