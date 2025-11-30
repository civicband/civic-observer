import json
from datetime import timedelta

import pytest
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from apikeys.models import APIKey
from tests.factories import UserFactory


@pytest.fixture
def valid_secret():
    """Fixture providing a valid service secret."""
    return "test-secret-123"


@pytest.fixture
def tailscale_ip():
    """Fixture providing a valid Tailscale IP."""
    return "100.64.1.1"


@pytest.fixture
def non_tailscale_ip():
    """Fixture providing a non-Tailscale IP."""
    return "192.168.1.1"


@pytest.mark.django_db
class TestValidateKeyView:
    def test_rejects_non_tailscale_ip(self, client, valid_secret):
        """Test rejects requests from non-Tailscale IPs with 403."""
        url = reverse("apikeys_internal:validate-key")

        with override_settings(CORKBOARD_SERVICE_SECRET=valid_secret):
            response = client.post(
                url,
                data=json.dumps({"api_key": "cb_live_test"}),
                content_type="application/json",
                REMOTE_ADDR="192.168.1.1",
                HTTP_X_SERVICE_SECRET=valid_secret,
            )

        assert response.status_code == 403
        assert response.json() == {"error": "Forbidden"}

    def test_rejects_missing_service_secret(self, client, tailscale_ip, valid_secret):
        """Test rejects requests without X-Service-Secret header with 401."""
        url = reverse("apikeys_internal:validate-key")

        with override_settings(CORKBOARD_SERVICE_SECRET=valid_secret):
            response = client.post(
                url,
                data=json.dumps({"api_key": "cb_live_test"}),
                content_type="application/json",
                REMOTE_ADDR=tailscale_ip,
            )

        assert response.status_code == 401
        assert response.json() == {"error": "Unauthorized"}

    def test_rejects_wrong_service_secret(self, client, tailscale_ip, valid_secret):
        """Test rejects requests with incorrect X-Service-Secret with 401."""
        url = reverse("apikeys_internal:validate-key")

        with override_settings(CORKBOARD_SERVICE_SECRET=valid_secret):
            response = client.post(
                url,
                data=json.dumps({"api_key": "cb_live_test"}),
                content_type="application/json",
                REMOTE_ADDR=tailscale_ip,
                HTTP_X_SERVICE_SECRET="wrong-secret",
            )

        assert response.status_code == 401
        assert response.json() == {"error": "Unauthorized"}

    def test_validates_valid_key(self, client, tailscale_ip, valid_secret):
        """Test validates a valid API key and returns success."""
        user = UserFactory()
        _, raw_key = APIKey.create_key(name="Test Key", user=user)

        url = reverse("apikeys_internal:validate-key")

        with override_settings(CORKBOARD_SERVICE_SECRET=valid_secret):
            response = client.post(
                url,
                data=json.dumps({"api_key": raw_key}),
                content_type="application/json",
                REMOTE_ADDR=tailscale_ip,
                HTTP_X_SERVICE_SECRET=valid_secret,
            )

        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is True
        assert "key_id" in data
        assert data["user_id"] == str(user.id)
        assert data["user_email"] == user.email

    def test_validates_key_without_user(self, client, tailscale_ip, valid_secret):
        """Test validates a valid API key without associated user."""
        _, raw_key = APIKey.create_key(name="Test Key", user=None)

        url = reverse("apikeys_internal:validate-key")

        with override_settings(CORKBOARD_SERVICE_SECRET=valid_secret):
            response = client.post(
                url,
                data=json.dumps({"api_key": raw_key}),
                content_type="application/json",
                REMOTE_ADDR=tailscale_ip,
                HTTP_X_SERVICE_SECRET=valid_secret,
            )

        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is True
        assert "key_id" in data
        assert "user_id" not in data
        assert "user_email" not in data

    def test_rejects_invalid_key_format(self, client, tailscale_ip, valid_secret):
        """Test rejects API key with invalid format."""
        url = reverse("apikeys_internal:validate-key")

        with override_settings(CORKBOARD_SERVICE_SECRET=valid_secret):
            response = client.post(
                url,
                data=json.dumps({"api_key": "invalid_format_key"}),
                content_type="application/json",
                REMOTE_ADDR=tailscale_ip,
                HTTP_X_SERVICE_SECRET=valid_secret,
            )

        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is False

    def test_rejects_non_existent_key(self, client, tailscale_ip, valid_secret):
        """Test rejects API key that doesn't exist in database."""
        url = reverse("apikeys_internal:validate-key")

        with override_settings(CORKBOARD_SERVICE_SECRET=valid_secret):
            response = client.post(
                url,
                data=json.dumps({"api_key": "cb_live_nonexistentkey123456"}),
                content_type="application/json",
                REMOTE_ADDR=tailscale_ip,
                HTTP_X_SERVICE_SECRET=valid_secret,
            )

        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is False

    def test_rejects_inactive_key(self, client, tailscale_ip, valid_secret):
        """Test rejects API key that has been revoked (is_active=False)."""
        _, raw_key = APIKey.create_key(name="Test Key")
        api_key = APIKey.objects.get(key_hash=APIKey.hash_key(raw_key))
        api_key.is_active = False
        api_key.save()

        url = reverse("apikeys_internal:validate-key")

        with override_settings(CORKBOARD_SERVICE_SECRET=valid_secret):
            response = client.post(
                url,
                data=json.dumps({"api_key": raw_key}),
                content_type="application/json",
                REMOTE_ADDR=tailscale_ip,
                HTTP_X_SERVICE_SECRET=valid_secret,
            )

        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is False

    def test_rejects_expired_key(self, client, tailscale_ip, valid_secret):
        """Test rejects API key that has expired."""
        expired_time = timezone.now() - timedelta(days=1)
        _, raw_key = APIKey.create_key(name="Test Key", expires_at=expired_time)

        url = reverse("apikeys_internal:validate-key")

        with override_settings(CORKBOARD_SERVICE_SECRET=valid_secret):
            response = client.post(
                url,
                data=json.dumps({"api_key": raw_key}),
                content_type="application/json",
                REMOTE_ADDR=tailscale_ip,
                HTTP_X_SERVICE_SECRET=valid_secret,
            )

        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is False

    def test_updates_last_used_at(self, client, tailscale_ip, valid_secret):
        """Test updates last_used_at timestamp on successful validation."""
        _, raw_key = APIKey.create_key(name="Test Key")
        api_key = APIKey.objects.get(key_hash=APIKey.hash_key(raw_key))

        # Ensure last_used_at is initially None
        assert api_key.last_used_at is None

        url = reverse("apikeys_internal:validate-key")

        with override_settings(CORKBOARD_SERVICE_SECRET=valid_secret):
            response = client.post(
                url,
                data=json.dumps({"api_key": raw_key}),
                content_type="application/json",
                REMOTE_ADDR=tailscale_ip,
                HTTP_X_SERVICE_SECRET=valid_secret,
            )

        assert response.status_code == 200

        # Refresh and check last_used_at was updated
        api_key.refresh_from_db()
        assert api_key.last_used_at is not None
        assert api_key.last_used_at <= timezone.now()

    def test_uses_x_forwarded_for_header(self, client, valid_secret):
        """Test uses X-Forwarded-For header for IP detection."""
        _, raw_key = APIKey.create_key(name="Test Key")

        url = reverse("apikeys_internal:validate-key")

        with override_settings(CORKBOARD_SERVICE_SECRET=valid_secret):
            response = client.post(
                url,
                data=json.dumps({"api_key": raw_key}),
                content_type="application/json",
                REMOTE_ADDR="10.0.0.1",  # Non-Tailscale IP
                HTTP_X_FORWARDED_FOR="100.64.1.1",  # Tailscale IP
                HTTP_X_SERVICE_SECRET=valid_secret,
            )

        assert response.status_code == 200
        assert response.json()["valid"] is True

    def test_rejects_empty_api_key(self, client, tailscale_ip, valid_secret):
        """Test rejects request with empty api_key."""
        url = reverse("apikeys_internal:validate-key")

        with override_settings(CORKBOARD_SERVICE_SECRET=valid_secret):
            response = client.post(
                url,
                data=json.dumps({"api_key": ""}),
                content_type="application/json",
                REMOTE_ADDR=tailscale_ip,
                HTTP_X_SERVICE_SECRET=valid_secret,
            )

        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is False

    def test_rejects_missing_api_key(self, client, tailscale_ip, valid_secret):
        """Test rejects request without api_key field."""
        url = reverse("apikeys_internal:validate-key")

        with override_settings(CORKBOARD_SERVICE_SECRET=valid_secret):
            response = client.post(
                url,
                data=json.dumps({}),
                content_type="application/json",
                REMOTE_ADDR=tailscale_ip,
                HTTP_X_SERVICE_SECRET=valid_secret,
            )

        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is False

    def test_rejects_invalid_json(self, client, tailscale_ip, valid_secret):
        """Test rejects request with invalid JSON body."""
        url = reverse("apikeys_internal:validate-key")

        with override_settings(CORKBOARD_SERVICE_SECRET=valid_secret):
            response = client.post(
                url,
                data="invalid json",
                content_type="application/json",
                REMOTE_ADDR=tailscale_ip,
                HTTP_X_SERVICE_SECRET=valid_secret,
            )

        assert response.status_code == 400
        data = response.json()
        assert data["error"] == "Invalid request"

    def test_csrf_exempt(self, client, tailscale_ip, valid_secret):
        """Test endpoint is CSRF exempt (for service-to-service calls)."""
        _, raw_key = APIKey.create_key(name="Test Key")

        url = reverse("apikeys_internal:validate-key")

        # Don't include CSRF token
        with override_settings(CORKBOARD_SERVICE_SECRET=valid_secret):
            response = client.post(
                url,
                data=json.dumps({"api_key": raw_key}),
                content_type="application/json",
                REMOTE_ADDR=tailscale_ip,
                HTTP_X_SERVICE_SECRET=valid_secret,
            )

        # Should succeed without CSRF token
        assert response.status_code == 200


@pytest.mark.django_db
class TestTailscaleIPValidation:
    """Test the is_tailscale_ip helper function through the view."""

    def test_accepts_valid_tailscale_range(self, client, valid_secret):
        """Test accepts IPs in valid Tailscale CGNAT range."""
        _, raw_key = APIKey.create_key(name="Test Key")
        url = reverse("apikeys_internal:validate-key")

        # Test various IPs in the 100.64.0.0/10 range
        valid_ips = [
            "100.64.0.1",
            "100.64.255.255",
            "100.100.1.1",
            "100.127.255.254",
        ]

        for ip in valid_ips:
            with override_settings(CORKBOARD_SERVICE_SECRET=valid_secret):
                response = client.post(
                    url,
                    data=json.dumps({"api_key": raw_key}),
                    content_type="application/json",
                    REMOTE_ADDR=ip,
                    HTTP_X_SERVICE_SECRET=valid_secret,
                )
            assert response.status_code == 200, f"Failed for IP {ip}"

    def test_rejects_invalid_tailscale_range(self, client, valid_secret):
        """Test rejects IPs outside Tailscale CGNAT range."""
        url = reverse("apikeys_internal:validate-key")

        # Test IPs outside the range
        invalid_ips = [
            "100.63.255.255",  # Just below range
            "100.128.0.0",  # Just above range
            "192.168.1.1",  # Private IP
            "8.8.8.8",  # Public IP
            "10.0.0.1",  # Private IP
        ]

        for ip in invalid_ips:
            with override_settings(CORKBOARD_SERVICE_SECRET=valid_secret):
                response = client.post(
                    url,
                    data=json.dumps({"api_key": "cb_live_test"}),
                    content_type="application/json",
                    REMOTE_ADDR=ip,
                    HTTP_X_SERVICE_SECRET=valid_secret,
                )
            assert response.status_code == 403, f"Should reject IP {ip}"
