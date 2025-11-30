"""Tests for channel disabled email notifications."""

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
        assert channel.get_platform_display() in mail.outbox[0].body

    def test_no_email_before_max_failures(self):
        """Test no email sent before reaching max failures."""
        from tests.factories import NotificationChannelFactory

        channel = NotificationChannelFactory(failure_count=0, is_enabled=True)

        channel.record_failure()

        assert len(mail.outbox) == 0
        assert channel.is_enabled is True
