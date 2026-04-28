"""Tests for digest subscription views."""

import pytest
from django.urls import reverse

from notifications.models import DigestSubscription
from tests.factories import MuniFactory, UserFactory


@pytest.mark.django_db
class TestDigestSubscriptionListView:
    def test_requires_login(self, client):
        response = client.get(reverse("notifications:digest-list"))
        assert response.status_code == 302

    def test_shows_user_subscriptions(self, client):
        user = UserFactory()
        muni = MuniFactory()
        DigestSubscription.objects.create(user=user, municipality=muni)
        client.force_login(user)
        response = client.get(reverse("notifications:digest-list"))
        assert response.status_code == 200
        assert muni.name in response.content.decode()

    def test_does_not_show_other_users_subscriptions(self, client):
        user1 = UserFactory()
        user2 = UserFactory(email="other@example.com")
        muni = MuniFactory()
        DigestSubscription.objects.create(user=user2, municipality=muni)
        client.force_login(user1)
        response = client.get(reverse("notifications:digest-list"))
        assert muni.name not in response.content.decode()


@pytest.mark.django_db
class TestDigestSubscriptionCreateView:
    def test_requires_login(self, client):
        muni = MuniFactory()
        response = client.post(
            reverse("notifications:digest-create"), {"municipality": muni.pk}
        )
        assert response.status_code == 302

    def test_creates_subscription(self, client):
        user = UserFactory()
        muni = MuniFactory()
        client.force_login(user)
        response = client.post(
            reverse("notifications:digest-create"),
            {"municipality": muni.pk},
        )
        assert response.status_code == 302
        assert (
            DigestSubscription.objects.filter(user=user, municipality=muni).count() == 1
        )

    def test_htmx_request_returns_row(self, client):
        user = UserFactory()
        muni = MuniFactory()
        client.force_login(user)
        response = client.post(
            reverse("notifications:digest-create"),
            {"municipality": muni.pk},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200
        assert str(muni.name) in response.content.decode()


@pytest.mark.django_db
class TestDigestSubscriptionDeleteView:
    def test_requires_login(self, client):
        user = UserFactory()
        muni = MuniFactory()
        sub = DigestSubscription.objects.create(user=user, municipality=muni)
        response = client.post(
            reverse("notifications:digest-delete", kwargs={"pk": sub.pk})
        )
        assert response.status_code == 302

    def test_deletes_subscription(self, client):
        user = UserFactory()
        muni = MuniFactory()
        sub = DigestSubscription.objects.create(user=user, municipality=muni)
        client.force_login(user)
        response = client.post(
            reverse("notifications:digest-delete", kwargs={"pk": sub.pk})
        )
        assert response.status_code == 302
        assert DigestSubscription.objects.filter(pk=sub.pk).count() == 0

    def test_cannot_delete_other_users_subscription(self, client):
        user1 = UserFactory()
        user2 = UserFactory(email="other@example.com")
        muni = MuniFactory()
        sub = DigestSubscription.objects.create(user=user2, municipality=muni)
        client.force_login(user1)
        response = client.post(
            reverse("notifications:digest-delete", kwargs={"pk": sub.pk})
        )
        assert response.status_code == 404
        assert DigestSubscription.objects.filter(pk=sub.pk).count() == 1
