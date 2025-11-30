import pytest
from django.urls import reverse


@pytest.mark.django_db
class TestChannelListView:
    def test_requires_login(self, client):
        """Test view requires authentication."""
        url = reverse("notifications:channel-list")
        response = client.get(url)

        assert response.status_code == 302

    def test_shows_user_channels(self, client):
        """Test view shows user's channels."""
        from tests.factories import NotificationChannelFactory, UserFactory

        user = UserFactory()
        NotificationChannelFactory(user=user, platform="discord")

        client.force_login(user)
        url = reverse("notifications:channel-list")
        response = client.get(url)

        assert response.status_code == 200
        assert b"discord" in response.content.lower()


@pytest.mark.django_db
class TestChannelCreateView:
    def test_can_create_channel(self, client):
        """Test creating a new channel."""
        from tests.factories import UserFactory

        user = UserFactory()
        client.force_login(user)

        url = reverse("notifications:channel-create")
        response = client.post(
            url,
            {
                "platform": "slack",
                "handle": "https://hooks.slack.com/services/T00/B00/xxx",
            },
        )

        assert response.status_code in [200, 302]

    def test_validates_handle_format(self, client):
        """Test that handle is validated."""
        from tests.factories import UserFactory

        user = UserFactory()
        client.force_login(user)

        url = reverse("notifications:channel-create")
        response = client.post(
            url,
            {
                "platform": "slack",
                "handle": "not-a-valid-webhook",
            },
        )

        # Should show validation error
        assert response.status_code == 200
        assert (
            b"error" in response.content.lower()
            or b"invalid" in response.content.lower()
        )


@pytest.mark.django_db
class TestChannelDeleteView:
    def test_can_delete_own_channel(self, client):
        """Test deleting own channel."""
        from notifications.models import NotificationChannel
        from tests.factories import NotificationChannelFactory, UserFactory

        user = UserFactory()
        channel = NotificationChannelFactory(user=user)

        client.force_login(user)
        url = reverse("notifications:channel-delete", args=[channel.pk])
        response = client.post(url)

        assert response.status_code in [200, 302]
        assert not NotificationChannel.objects.filter(pk=channel.pk).exists()

    def test_cannot_delete_other_users_channel(self, client):
        """Test cannot delete another user's channel."""
        from notifications.models import NotificationChannel
        from tests.factories import NotificationChannelFactory, UserFactory

        user = UserFactory()
        other_user = UserFactory()
        channel = NotificationChannelFactory(user=other_user)

        client.force_login(user)
        url = reverse("notifications:channel-delete", args=[channel.pk])
        response = client.post(url)

        assert response.status_code == 404
        assert NotificationChannel.objects.filter(pk=channel.pk).exists()
