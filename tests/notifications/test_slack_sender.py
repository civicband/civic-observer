from unittest.mock import MagicMock, patch

import pytest

from notifications.senders.base import (
    HTTP_TIMEOUT_SECONDS,
    RETRY_AFTER_CAP_SECONDS,
    parse_retry_after,
)
from notifications.senders.slack import SlackSender


class TestParseRetryAfter:
    def test_default_when_header_missing(self):
        response = MagicMock()
        response.headers = {}
        assert parse_retry_after(response, default=2.0) == 2.0

    def test_parses_seconds(self):
        response = MagicMock()
        response.headers = {"Retry-After": "3"}
        assert parse_retry_after(response) == 3.0

    def test_caps_large_values(self):
        response = MagicMock()
        response.headers = {"Retry-After": "120"}
        assert parse_retry_after(response) == RETRY_AFTER_CAP_SECONDS

    def test_default_when_unparseable(self):
        response = MagicMock()
        response.headers = {"Retry-After": "not-a-number"}
        assert parse_retry_after(response, default=1.5) == 1.5


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

    @patch("notifications.senders.base.time.sleep")
    @patch("notifications.senders.slack.httpx.Client")
    def test_retries_once_on_rate_limit(self, mock_client_class, mock_sleep):
        """A 429 should be retried once (honoring Retry-After), not counted as failure."""
        from tests.factories import NotificationChannelFactory

        mock_client = MagicMock()
        rate_limited = MagicMock()
        rate_limited.status_code = 429
        rate_limited.headers = {"Retry-After": "1"}
        ok = MagicMock()
        ok.status_code = 200
        ok.headers = {}
        mock_client.post.side_effect = [rate_limited, ok]
        mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

        channel = NotificationChannelFactory(
            platform="slack",
            handle="https://hooks.slack.com/services/T00/B00/xxx",
        )

        result = SlackSender().send(channel, "Test notification message")

        assert result is True
        assert mock_client.post.call_count == 2
        mock_sleep.assert_called_once_with(1.0)

    @patch("notifications.senders.slack.httpx.Client")
    def test_client_is_created_with_timeout(self, mock_client_class):
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
        SlackSender().send(channel, "Test notification message")

        assert mock_client_class.call_args.kwargs.get("timeout") == HTTP_TIMEOUT_SECONDS
