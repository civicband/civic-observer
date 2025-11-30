from notifications.senders.registry import get_sender


class TestSenderRegistry:
    def test_get_discord_sender(self):
        """Test getting Discord sender."""
        from notifications.senders.discord import DiscordSender

        sender = get_sender("discord")
        assert isinstance(sender, DiscordSender)

    def test_get_slack_sender(self):
        """Test getting Slack sender."""
        from notifications.senders.slack import SlackSender

        sender = get_sender("slack")
        assert isinstance(sender, SlackSender)

    def test_get_bluesky_sender(self):
        """Test getting Bluesky sender."""
        from notifications.senders.bluesky import BlueskySender

        sender = get_sender("bluesky")
        assert isinstance(sender, BlueskySender)

    def test_get_mastodon_sender(self):
        """Test getting Mastodon sender."""
        from notifications.senders.mastodon import MastodonSender

        sender = get_sender("mastodon")
        assert isinstance(sender, MastodonSender)

    def test_get_unknown_sender_returns_none(self):
        """Test getting unknown sender returns None."""
        sender = get_sender("unknown_platform")
        assert sender is None

    def test_senders_are_cached(self):
        """Test that sender instances are cached."""
        sender1 = get_sender("discord")
        sender2 = get_sender("discord")
        assert sender1 is sender2
