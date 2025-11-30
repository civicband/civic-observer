from unittest.mock import patch

import pytest
from django.core import mail


@pytest.mark.django_db
class TestNotificationIntegration:
    @patch("notifications.senders.discord.DiscordSender.send")
    def test_immediate_notification_sends_to_channels(self, mock_discord):
        """Test immediate notifications are sent to configured channels."""
        from searches.tasks import check_saved_search_for_updates
        from tests.factories import (
            MeetingDocumentFactory,
            MeetingPageFactory,
            NotificationChannelFactory,
            SavedSearchFactory,
            SearchFactory,
        )

        mock_discord.return_value = True

        # Create user with Discord channel
        search = SearchFactory(search_term="budget")
        saved_search = SavedSearchFactory(
            search=search,
            notification_frequency="immediate",
        )
        NotificationChannelFactory(
            user=saved_search.user,
            platform="discord",
            is_enabled=True,
        )

        # Create matching page
        doc = MeetingDocumentFactory()
        search.municipalities.add(doc.municipality)
        MeetingPageFactory(
            document=doc,
            text="Budget discussion for 2025.",
        )

        # Trigger check
        check_saved_search_for_updates(saved_search.id)

        # Email should still be sent (fallback)
        assert len(mail.outbox) == 1

        # Discord should also be called
        mock_discord.assert_called_once()

    @patch("notifications.senders.discord.DiscordSender.send")
    def test_channel_failure_falls_back_to_email(self, mock_discord):
        """Test that channel failure still sends email."""
        from searches.tasks import check_saved_search_for_updates
        from tests.factories import (
            MeetingDocumentFactory,
            MeetingPageFactory,
            NotificationChannelFactory,
            SavedSearchFactory,
            SearchFactory,
        )

        mock_discord.return_value = False  # Discord fails

        search = SearchFactory(search_term="budget")
        saved_search = SavedSearchFactory(
            search=search,
            notification_frequency="immediate",
        )
        NotificationChannelFactory(
            user=saved_search.user,
            platform="discord",
            is_enabled=True,
        )

        doc = MeetingDocumentFactory()
        search.municipalities.add(doc.municipality)
        MeetingPageFactory(
            document=doc,
            text="Budget discussion for 2025.",
        )

        check_saved_search_for_updates(saved_search.id)

        # Email should still be sent
        assert len(mail.outbox) == 1

    def test_no_channels_configured_sends_email_only(self):
        """Test that notification works with email only when no channels."""
        from searches.tasks import check_saved_search_for_updates
        from tests.factories import (
            MeetingDocumentFactory,
            MeetingPageFactory,
            SavedSearchFactory,
            SearchFactory,
        )

        search = SearchFactory(search_term="budget")
        saved_search = SavedSearchFactory(
            search=search,
            notification_frequency="immediate",
        )

        doc = MeetingDocumentFactory()
        search.municipalities.add(doc.municipality)
        MeetingPageFactory(
            document=doc,
            text="Budget discussion for 2025.",
        )

        check_saved_search_for_updates(saved_search.id)

        # Email sent
        assert len(mail.outbox) == 1
