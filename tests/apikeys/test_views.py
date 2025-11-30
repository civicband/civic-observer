import pytest
from django.urls import reverse

from apikeys.models import APIKey
from tests.factories import APIKeyFactory, UserFactory


@pytest.mark.django_db
class TestAPIKeyListView:
    def test_requires_login(self, client):
        """Test that unauthenticated users are redirected to login."""
        url = reverse("apikeys:list")
        response = client.get(url)

        assert response.status_code == 302
        assert "/login" in response.url or "/stagedoor" in response.url

    def test_shows_user_api_keys(self, client):
        """Test that view shows only user's API keys."""
        user = UserFactory()
        other_user = UserFactory()

        APIKeyFactory(user=user, name="My Production Key")
        APIKeyFactory(user=other_user, name="Other User Key")

        client.force_login(user)
        url = reverse("apikeys:list")
        response = client.get(url)

        assert response.status_code == 200
        assert "My Production Key" in response.content.decode()
        assert "Other User Key" not in response.content.decode()

    def test_shows_create_form(self, client):
        """Test that view includes create form in context."""
        user = UserFactory()
        client.force_login(user)

        url = reverse("apikeys:list")
        response = client.get(url)

        assert response.status_code == 200
        assert "form" in response.context
        assert "Create API Key" in response.content.decode()

    def test_empty_state(self, client):
        """Test empty state message when no API keys."""
        user = UserFactory()
        client.force_login(user)

        url = reverse("apikeys:list")
        response = client.get(url)

        assert "No API keys yet" in response.content.decode()


@pytest.mark.django_db
class TestAPIKeyCreateView:
    def test_requires_login(self, client):
        """Test that unauthenticated users are redirected."""
        url = reverse("apikeys:create")
        response = client.post(url, {"name": "Test Key"})

        assert response.status_code == 302

    def test_creates_api_key(self, client):
        """Test POST creates an API key for the user."""
        user = UserFactory()
        client.force_login(user)

        url = reverse("apikeys:create")
        response = client.post(url, {"name": "Production Server"})

        assert response.status_code == 200
        assert APIKey.objects.filter(user=user, name="Production Server").exists()

    def test_returns_modal_with_raw_key(self, client):
        """Test successful creation returns modal with raw key."""
        user = UserFactory()
        client.force_login(user)

        url = reverse("apikeys:create")
        response = client.post(url, {"name": "Test Key"})

        content = response.content.decode()
        assert response.status_code == 200
        assert "API Key Created" in content
        assert "cb_live_" in content
        assert "Copy to Clipboard" in content

    def test_stores_key_in_session(self, client):
        """Test raw key is stored in session for download."""
        user = UserFactory()
        client.force_login(user)

        url = reverse("apikeys:create")
        response = client.post(url, {"name": "Test Key"})

        assert response.status_code == 200
        assert "new_api_key" in client.session
        assert client.session["new_api_key"].startswith("cb_live_")

    def test_invalid_form_returns_error(self, client):
        """Test invalid form returns error response."""
        user = UserFactory()
        client.force_login(user)

        url = reverse("apikeys:create")
        response = client.post(url, {})  # Missing required 'name' field

        assert response.status_code == 400
        assert "Create API Key" in response.content.decode()


@pytest.mark.django_db
class TestAPIKeyRevokeView:
    def test_requires_login(self, client):
        """Test that unauthenticated users are redirected."""
        api_key = APIKeyFactory()
        url = reverse("apikeys:revoke", args=[api_key.pk])
        response = client.post(url)

        assert response.status_code == 302

    def test_revokes_own_key(self, client):
        """Test user can revoke their own API key."""
        user = UserFactory()
        api_key = APIKeyFactory(user=user, is_active=True)

        client.force_login(user)
        url = reverse("apikeys:revoke", args=[api_key.pk])
        response = client.post(url)

        api_key.refresh_from_db()
        assert response.status_code == 302
        assert api_key.is_active is False

    def test_cannot_revoke_other_users_key(self, client):
        """Test user cannot revoke another user's API key."""
        user = UserFactory()
        other_user = UserFactory()
        api_key = APIKeyFactory(user=other_user, is_active=True)

        client.force_login(user)
        url = reverse("apikeys:revoke", args=[api_key.pk])
        response = client.post(url)

        api_key.refresh_from_db()
        assert response.status_code == 404
        assert api_key.is_active is True

    def test_redirects_to_list(self, client):
        """Test successful revoke redirects to list."""
        user = UserFactory()
        api_key = APIKeyFactory(user=user)

        client.force_login(user)
        url = reverse("apikeys:revoke", args=[api_key.pk])
        response = client.post(url)

        assert response.status_code == 302
        assert reverse("apikeys:list") in response.url


@pytest.mark.django_db
class TestAPIKeyDeleteView:
    def test_requires_login(self, client):
        """Test that unauthenticated users are redirected."""
        api_key = APIKeyFactory()
        url = reverse("apikeys:delete", args=[api_key.pk])
        response = client.post(url)

        assert response.status_code == 302

    def test_deletes_own_key(self, client):
        """Test user can delete their own API key."""
        user = UserFactory()
        api_key = APIKeyFactory(user=user)
        api_key_pk = api_key.pk

        client.force_login(user)
        url = reverse("apikeys:delete", args=[api_key.pk])
        response = client.post(url)

        assert response.status_code == 302
        assert not APIKey.objects.filter(pk=api_key_pk).exists()

    def test_cannot_delete_other_users_key(self, client):
        """Test user cannot delete another user's API key."""
        user = UserFactory()
        other_user = UserFactory()
        api_key = APIKeyFactory(user=other_user)
        api_key_pk = api_key.pk

        client.force_login(user)
        url = reverse("apikeys:delete", args=[api_key.pk])
        response = client.post(url)

        assert response.status_code == 404
        assert APIKey.objects.filter(pk=api_key_pk).exists()

    def test_redirects_to_list(self, client):
        """Test successful delete redirects to list."""
        user = UserFactory()
        api_key = APIKeyFactory(user=user)

        client.force_login(user)
        url = reverse("apikeys:delete", args=[api_key.pk])
        response = client.post(url)

        assert response.status_code == 302
        assert reverse("apikeys:list") in response.url


@pytest.mark.django_db
class TestAPIKeyDownloadView:
    def test_requires_login(self, client):
        """Test that unauthenticated users are redirected."""
        url = reverse("apikeys:download")
        response = client.get(url)

        assert response.status_code == 302

    def test_downloads_key_from_session(self, client):
        """Test downloading key stored in session."""
        user = UserFactory()
        client.force_login(user)

        # Store key in session
        session = client.session
        session["new_api_key"] = "cb_live_test123456"
        session.save()

        url = reverse("apikeys:download")
        response = client.get(url)

        assert response.status_code == 200
        assert response["Content-Type"] == "text/plain"
        assert (
            response["Content-Disposition"]
            == 'attachment; filename="civicband-api-key.txt"'
        )
        assert response.content == b"cb_live_test123456"

    def test_key_removed_from_session_after_download(self, client):
        """Test key is removed from session after download."""
        user = UserFactory()
        client.force_login(user)

        # Store key in session
        session = client.session
        session["new_api_key"] = "cb_live_test123456"
        session.save()

        url = reverse("apikeys:download")
        client.get(url)

        # Key should be removed
        assert "new_api_key" not in client.session

    def test_returns_404_when_no_key_in_session(self, client):
        """Test returns 404 when no key in session."""
        user = UserFactory()
        client.force_login(user)

        url = reverse("apikeys:download")
        response = client.get(url)

        assert response.status_code == 404
        assert b"Key no longer available" in response.content
