import json
import os
from unittest.mock import Mock, patch

import httpx
import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import override_settings
from django.urls import reverse

from .models import Muni

User = get_user_model()


@pytest.mark.django_db
class TestMuniModel:
    def test_create_muni(self):
        muni = Muni.objects.create(
            subdomain="testcity",
            name="Test City",
            state="CA",
            country="US",
            kind="city",
            pages=100,
        )
        assert muni.subdomain == "testcity"
        assert muni.name == "Test City"
        assert muni.state == "CA"
        assert muni.country == "US"
        assert muni.kind == "city"
        assert muni.pages == 100
        assert muni.created is not None
        assert muni.modified is not None
        assert str(muni.id)  # UUID is valid

    def test_str_representation(self):
        muni = Muni(name="San Francisco", state="CA")
        assert str(muni) == "San Francisco, CA"

    def test_subdomain_unique(self):
        Muni.objects.create(
            subdomain="testcity", name="Test City", state="CA", kind="city"
        )

        with pytest.raises(IntegrityError):
            Muni.objects.create(
                subdomain="testcity", name="Another City", state="NY", kind="city"
            )

    def test_default_values(self):
        muni = Muni.objects.create(
            subdomain="testmuni", name="Test Municipality", state="TX", kind="city"
        )
        assert muni.country == "US"
        assert muni.pages == 0
        assert muni.latitude is None
        assert muni.longitude is None
        assert muni.popup_data is None

    def test_optional_fields(self):
        muni = Muni.objects.create(
            subdomain="fulltest",
            name="Full Test City",
            state="NY",
            kind="city",
            latitude=40.7128,
            longitude=-74.0060,
            popup_data={"population": 8000000},
        )
        assert muni.latitude == 40.7128
        assert muni.longitude == -74.0060
        assert muni.popup_data == {"population": 8000000}

    def test_meta_options(self):
        assert Muni._meta.verbose_name == "Municipality"
        assert Muni._meta.verbose_name_plural == "Municipalities"
        assert Muni._meta.ordering == ["name"]


@pytest.mark.django_db
class TestMuniCRUDViews:
    @pytest.fixture
    def muni(self):
        return Muni.objects.create(
            subdomain="testcity",
            name="Test City",
            state="CA",
            country="US",
            kind="city",
            pages=100,
        )

    @pytest.fixture
    def user(self):
        return User.objects.create_user(  # type: ignore
            username="testuser", email="test@example.com", password="testpass123"
        )

    def test_list_view_public_access(self, client, muni):
        """Test that list view is accessible without authentication"""
        url = reverse("munis:muni-list")
        response = client.get(url)
        assert response.status_code == 200

    def test_detail_view_public_access(self, client, muni):
        """Test that detail view is accessible without authentication"""
        url = reverse("munis:muni-detail", kwargs={"pk": muni.pk})
        response = client.get(url)
        assert response.status_code == 200

    def test_create_view_requires_auth(self, client):
        """Test that create view redirects to login for unauthenticated users"""
        url = reverse("munis:muni-create")
        response = client.get(url)
        assert response.status_code == 302
        assert settings.LOGIN_URL in response.url

    def test_create_post_requires_auth(self, client):
        """Test that POST to create view redirects to login for unauthenticated users"""
        url = reverse("munis:muni-create")
        data = {
            "subdomain": "newcity",
            "name": "New City",
            "state": "NY",
            "kind": "city",
        }
        response = client.post(url, data)
        assert response.status_code == 302
        assert settings.LOGIN_URL in response.url

    def test_update_view_requires_auth(self, client, muni):
        """Test that update view redirects to login for unauthenticated users"""
        url = reverse("munis:muni-update", kwargs={"pk": muni.pk})
        response = client.get(url)
        assert response.status_code == 302
        assert settings.LOGIN_URL in response.url

    def test_update_post_requires_auth(self, client, muni):
        """Test that POST to update view redirects to login for unauthenticated users"""
        url = reverse("munis:muni-update", kwargs={"pk": muni.pk})
        data = {
            "subdomain": "updatedcity",
            "name": "Updated City",
            "state": "CA",
            "kind": "city",
        }
        response = client.post(url, data)
        assert response.status_code == 302
        assert settings.LOGIN_URL in response.url

    def test_delete_view_requires_auth(self, client, muni):
        """Test that delete view redirects to login for unauthenticated users"""
        url = reverse("munis:muni-delete", kwargs={"pk": muni.pk})
        response = client.get(url)
        assert response.status_code == 302
        assert settings.LOGIN_URL in response.url

    def test_delete_post_requires_auth(self, client, muni):
        """Test that POST to delete view redirects to login for unauthenticated users"""
        url = reverse("munis:muni-delete", kwargs={"pk": muni.pk})
        response = client.post(url)
        assert response.status_code == 302
        assert settings.LOGIN_URL in response.url

    def test_create_view_authenticated_access(self, client, user):
        """Test that authenticated users can access create view"""
        client.force_login(user)
        url = reverse("munis:muni-create")
        response = client.get(url)
        assert response.status_code == 200

    def test_create_post_authenticated_success(self, client, user):
        """Test that authenticated users can create municipalities"""
        client.force_login(user)
        url = reverse("munis:muni-create")
        data = {
            "subdomain": "authcity",
            "name": "Auth City",
            "state": "TX",
            "kind": "city",
            "country": "US",
            "pages": "0",
        }
        response = client.post(url, data)
        assert response.status_code == 302  # Redirect after successful creation
        assert Muni.objects.filter(subdomain="authcity").exists()

    def test_update_view_authenticated_access(self, client, user, muni):
        """Test that authenticated users can access update view"""
        client.force_login(user)
        url = reverse("munis:muni-update", kwargs={"pk": muni.pk})
        response = client.get(url)
        assert response.status_code == 200

    def test_update_post_authenticated_success(self, client, user, muni):
        """Test that authenticated users can update municipalities"""
        client.force_login(user)
        url = reverse("munis:muni-update", kwargs={"pk": muni.pk})
        data = {
            "subdomain": muni.subdomain,
            "name": "Updated Test City",
            "state": "CA",
            "kind": "city",
            "country": "US",
            "pages": "100",
        }
        response = client.post(url, data)
        assert response.status_code == 302  # Redirect after successful update
        muni.refresh_from_db()
        assert muni.name == "Updated Test City"

    def test_delete_view_authenticated_access(self, client, user, muni):
        """Test that authenticated users can access delete view"""
        client.force_login(user)
        url = reverse("munis:muni-delete", kwargs={"pk": muni.pk})
        response = client.get(url)
        assert response.status_code == 200

    def test_delete_post_authenticated_success(self, client, user, muni):
        """Test that authenticated users can delete municipalities"""
        client.force_login(user)
        muni_pk = muni.pk
        url = reverse("munis:muni-delete", kwargs={"pk": muni.pk})
        response = client.post(url)
        assert response.status_code == 302  # Redirect after successful deletion
        assert not Muni.objects.filter(pk=muni_pk).exists()


@pytest.mark.django_db
class TestMuniWebhookUpdateView:
    @pytest.fixture
    def webhook_data(self):
        return {
            "name": "Webhook City",
            "state": "CA",
            "country": "US",
            "kind": "city",
            "pages": 50,
        }

    def test_create_muni_without_auth(self, client, webhook_data):
        """Test creating a new municipality without webhook secret"""
        url = reverse("munis:muni-webhook-update", kwargs={"subdomain": "webhookcity"})
        response = client.post(
            url, json.dumps(webhook_data), content_type="application/json"
        )
        assert response.status_code == 401

    def test_update_existing_muni_without_auth(self, client, webhook_data):
        """Test updating an existing municipality without webhook secret"""
        # Create existing muni
        Muni.objects.create(
            subdomain="webhookcity", name="Original City", state="NY", kind="city"
        )

        url = reverse("munis:muni-webhook-update", kwargs={"subdomain": "webhookcity"})
        webhook_data["name"] = "Updated Webhook City"
        response = client.post(
            url, json.dumps(webhook_data), content_type="application/json"
        )

        assert response.status_code == 401

    @override_settings()
    def test_create_muni_with_valid_webhook_secret(self, client, webhook_data):
        """Test creating municipality with valid webhook secret"""
        os.environ["WEBHOOK_SECRET"] = "test-secret-123"

        url = reverse("munis:muni-webhook-update", kwargs={"subdomain": "secretcity"})
        headers = {"Authorization": "Bearer test-secret-123"}
        response = client.post(
            url,
            json.dumps(webhook_data),
            content_type="application/json",
            **{f"HTTP_{k.upper().replace('-', '_')}": v for k, v in headers.items()},
        )

        assert response.status_code == 201
        data = response.json()
        assert data["action"] == "created"
        assert Muni.objects.filter(subdomain="secretcity").exists()

        # Clean up
        if "WEBHOOK_SECRET" in os.environ:
            del os.environ["WEBHOOK_SECRET"]

    @override_settings()
    def test_create_muni_with_invalid_webhook_secret(self, client, webhook_data):
        """Test creating municipality with invalid webhook secret"""
        os.environ["WEBHOOK_SECRET"] = "test-secret-123"

        url = reverse("munis:muni-webhook-update", kwargs={"subdomain": "secretcity"})
        headers = {"Authorization": "Bearer wrong-secret"}
        response = client.post(
            url,
            json.dumps(webhook_data),
            content_type="application/json",
            **{f"HTTP_{k.upper().replace('-', '_')}": v for k, v in headers.items()},
        )

        assert response.status_code == 401
        data = response.json()
        assert data["error"] == "Invalid webhook secret"
        assert not Muni.objects.filter(subdomain="secretcity").exists()

        # Clean up
        if "WEBHOOK_SECRET" in os.environ:
            del os.environ["WEBHOOK_SECRET"]

    @override_settings()
    def test_webhook_secret_missing_auth_header(self, client, webhook_data):
        """Test webhook with secret configured but no auth header provided"""
        os.environ["WEBHOOK_SECRET"] = "test-secret-123"

        url = reverse("munis:muni-webhook-update", kwargs={"subdomain": "secretcity"})
        response = client.post(
            url, json.dumps(webhook_data), content_type="application/json"
        )

        assert response.status_code == 401
        data = response.json()
        assert data["error"] == "Invalid webhook secret"

        # Clean up
        if "WEBHOOK_SECRET" in os.environ:
            del os.environ["WEBHOOK_SECRET"]

    @override_settings()
    def test_webhook_with_put_method(self, client, webhook_data):
        """Test webhook endpoint accepts PUT requests"""
        os.environ["WEBHOOK_SECRET"] = "test-secret-123"

        url = reverse("munis:muni-webhook-update", kwargs={"subdomain": "putcity"})
        headers = {"Authorization": "Bearer test-secret-123"}
        response = client.put(
            url,
            json.dumps(webhook_data),
            content_type="application/json",
            headers=headers,
        )

        assert response.status_code == 201
        data = response.json()
        assert data["action"] == "created"
        assert Muni.objects.filter(subdomain="putcity").exists()

    @override_settings()
    def test_webhook_auth_direct_token(self, client, webhook_data):
        """Test webhook authentication with direct token (no Bearer prefix)"""
        os.environ["WEBHOOK_SECRET"] = "test-secret-123"

        url = reverse("munis:muni-webhook-update", kwargs={"subdomain": "directauth"})
        headers = {"Authorization": "test-secret-123"}  # Direct token without Bearer
        response = client.post(
            url,
            json.dumps(webhook_data),
            content_type="application/json",
            **{f"HTTP_{k.upper().replace('-', '_')}": v for k, v in headers.items()},
        )

        assert response.status_code == 201
        data = response.json()
        assert data["action"] == "created"
        assert Muni.objects.filter(subdomain="directauth").exists()

        # Clean up
        if "WEBHOOK_SECRET" in os.environ:
            del os.environ["WEBHOOK_SECRET"]

    @pytest.mark.skip
    @override_settings()
    def test_webhook_handles_exception(self, client):
        """Test webhook handles exceptions gracefully"""
        os.environ["WEBHOOK_SECRET"] = "test-secret-123"

        url = reverse("munis:muni-webhook-update", kwargs={"subdomain": "error-test"})
        headers = {"Authorization": "Bearer test-secret-123"}

        # Mock update_or_create to raise an exception
        with patch.object(
            Muni.objects, "update_or_create", side_effect=Exception("Database error")
        ):
            response = client.post(
                url,
                json.dumps({"name": "Error Test City"}),
                content_type="application/json",
                headers=headers,
            )

        assert response.status_code == 400
        data = response.json()
        assert data["error"] == "Database error"

        # Clean up
        if "WEBHOOK_SECRET" in os.environ:
            del os.environ["WEBHOOK_SECRET"]

    @override_settings()
    def test_webhook_invalid_json(self, client):
        """Test webhook with invalid JSON data"""
        os.environ["WEBHOOK_SECRET"] = "test-secret-123"

        url = reverse("munis:muni-webhook-update", kwargs={"subdomain": "invalidjson"})
        headers = {"Authorization": "Bearer test-secret-123"}
        response = client.post(
            url, "invalid json", content_type="application/json", headers=headers
        )

        assert response.status_code == 400
        data = response.json()
        assert data["error"] == "Invalid JSON"

    @override_settings()
    def test_webhook_missing_required_name(self, client):
        """Test webhook with missing required name field"""
        os.environ["WEBHOOK_SECRET"] = "test-secret-123"

        url = reverse("munis:muni-webhook-update", kwargs={"subdomain": "noname"})
        headers = {"Authorization": "Bearer test-secret-123"}

        data = {"state": "CA", "kind": "city"}
        response = client.post(
            url, json.dumps(data), content_type="application/json", headers=headers
        )

        assert response.status_code == 400
        response_data = response.json()
        assert response_data["error"] == "name field is required"

    @override_settings()
    def test_webhook_filters_invalid_fields(self, client):
        """Test webhook filters out invalid model fields"""
        os.environ["WEBHOOK_SECRET"] = "test-secret-123"

        url = reverse("munis:muni-webhook-update", kwargs={"subdomain": "filtered"})
        headers = {"Authorization": "Bearer test-secret-123"}
        data = {
            "name": "Filtered City",
            "state": "CA",
            "kind": "city",
            "invalid_field": "should be ignored",
            "another_invalid": 123,
        }
        response = client.post(
            url, json.dumps(data), content_type="application/json", headers=headers
        )

        assert response.status_code == 201
        data = response.json()
        assert data["action"] == "created"
        assert "invalid_field" not in data
        assert "another_invalid" not in data

    @override_settings()
    def test_webhook_updates_searches(self, client):
        """Test webhook updates saved searches"""
        os.environ["WEBHOOK_SECRET"] = "test-secret-123"

        url = reverse(
            "munis:muni-webhook-update", kwargs={"subdomain": "search-update"}
        )
        headers = {"Authorization": "Bearer test-secret-123"}
        data = {
            "name": "Search City",
            "state": "CA",
            "kind": "city",
            "saved_search_ids": [1, 2, 3],
        }

        mock_response = Mock()
        mock_response.status_code = 201
        mock_response.json.return_value = {
            "rows": [{"meeting": "Council", "date": "2024-01-01"}]
        }
        with patch.object(
            httpx,
            "get",
        ):
            response = client.post(
                url,
                json.dumps({"name": "Error Test City"}),
                content_type="application/json",
                headers=headers,
            )
        response = client.post(
            url, json.dumps(data), content_type="application/json", headers=headers
        )

        assert response.status_code == 200
        data = response.json()
        assert data["action"] == "updated"


@pytest.mark.django_db
class TestMuniAdminActions:
    """Test admin actions for municipalities."""

    @pytest.fixture
    def superuser(self):
        return User.objects.create_superuser(
            username="admin", email="admin@example.com", password="admin123"
        )

    @pytest.fixture
    def muni(self):
        return Muni.objects.create(
            subdomain="testcity.ca",
            name="Test City",
            state="CA",
            country="US",
            kind="city",
        )

    @patch("meetings.services.backfill_municipality_meetings")
    def test_backfill_meetings_action_success(self, mock_backfill, superuser, muni):
        """Test backfill meetings admin action succeeds"""
        from django.contrib.admin.sites import AdminSite

        from municipalities.admin import MuniAdmin

        # Mock successful backfill
        mock_backfill.return_value = {
            "documents_created": 5,
            "documents_updated": 2,
            "pages_created": 50,
            "pages_updated": 10,
            "errors": 0,
        }

        # Create admin instance and mock request
        site = AdminSite()
        admin = MuniAdmin(Muni, site)
        request = Mock()
        request.user = superuser

        # Call the action
        queryset = Muni.objects.filter(pk=muni.pk)
        admin.backfill_meetings(request, queryset)

        # Verify backfill was called
        assert mock_backfill.called
        assert mock_backfill.call_count == 1

    @patch("meetings.services.backfill_municipality_meetings")
    def test_backfill_meetings_action_with_errors(self, mock_backfill, superuser, muni):
        """Test backfill meetings admin action handles errors in stats"""
        from django.contrib.admin.sites import AdminSite

        from municipalities.admin import MuniAdmin

        # Mock backfill with errors
        mock_backfill.return_value = {
            "documents_created": 3,
            "documents_updated": 1,
            "pages_created": 20,
            "pages_updated": 5,
            "errors": 5,
        }

        site = AdminSite()
        admin = MuniAdmin(Muni, site)
        request = Mock()
        request.user = superuser

        queryset = Muni.objects.filter(pk=muni.pk)
        admin.backfill_meetings(request, queryset)

        # Verify it completed despite errors
        assert mock_backfill.called

    @patch("meetings.services.backfill_municipality_meetings")
    def test_backfill_meetings_action_exception(self, mock_backfill, superuser, muni):
        """Test backfill meetings admin action handles exceptions"""
        from django.contrib.admin.sites import AdminSite

        from municipalities.admin import MuniAdmin

        # Mock backfill raising exception
        mock_backfill.side_effect = Exception("Network error")

        site = AdminSite()
        admin = MuniAdmin(Muni, site)
        request = Mock()
        request.user = superuser

        queryset = Muni.objects.filter(pk=muni.pk)
        # Should not raise exception
        admin.backfill_meetings(request, queryset)

    @patch("meetings.services.backfill_municipality_meetings")
    def test_backfill_meetings_multiple_munis(self, mock_backfill, superuser):
        """Test backfill meetings action with multiple municipalities"""
        from django.contrib.admin.sites import AdminSite

        from municipalities.admin import MuniAdmin

        # Create multiple municipalities
        muni1 = Muni.objects.create(
            subdomain="city1.ca", name="City 1", state="CA", kind="city"
        )
        muni2 = Muni.objects.create(
            subdomain="city2.ca", name="City 2", state="NY", kind="city"
        )

        mock_backfill.return_value = {
            "documents_created": 2,
            "documents_updated": 0,
            "pages_created": 10,
            "pages_updated": 0,
            "errors": 0,
        }

        site = AdminSite()
        admin = MuniAdmin(Muni, site)
        request = Mock()
        request.user = superuser

        queryset = Muni.objects.filter(pk__in=[muni1.pk, muni2.pk])
        admin.backfill_meetings(request, queryset)

        # Verify backfill was called for both
        assert mock_backfill.call_count == 2
