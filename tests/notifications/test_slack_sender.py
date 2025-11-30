from unittest.mock import MagicMock, patch

import pytest

from notifications.senders.slack import SlackSender


class TestSlackSenderValidation:
    def test_valid_slack_webhook_url(self):
        """Test valid Slack webhook URL."""
        sender = SlackSender()

        assert (
            sender.validate_handle(
                "https://hooks.slack.com/services/TXXXXXXXXX/BXXXXXXXXX/testwebhookkey"
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
