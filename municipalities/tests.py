import json
import os

import pytest
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
            country="USA",
            kind="city",
            pages=100,
        )
        assert muni.subdomain == "testcity"
        assert muni.name == "Test City"
        assert muni.state == "CA"
        assert muni.country == "USA"
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
        assert muni.country == "USA"
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
            country="USA",
            kind="city",
            pages=100,
        )

    @pytest.fixture
    def user(self):
        return User.objects.create_user(
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
        assert "/accounts/login/" in response.url

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
        assert "/accounts/login/" in response.url

    def test_update_view_requires_auth(self, client, muni):
        """Test that update view redirects to login for unauthenticated users"""
        url = reverse("munis:muni-update", kwargs={"pk": muni.pk})
        response = client.get(url)
        assert response.status_code == 302
        assert "/accounts/login/" in response.url

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
        assert "/accounts/login/" in response.url

    def test_delete_view_requires_auth(self, client, muni):
        """Test that delete view redirects to login for unauthenticated users"""
        url = reverse("munis:muni-delete", kwargs={"pk": muni.pk})
        response = client.get(url)
        assert response.status_code == 302
        assert "/accounts/login/" in response.url

    def test_delete_post_requires_auth(self, client, muni):
        """Test that POST to delete view redirects to login for unauthenticated users"""
        url = reverse("munis:muni-delete", kwargs={"pk": muni.pk})
        response = client.post(url)
        assert response.status_code == 302
        assert "/accounts/login/" in response.url

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
            "country": "USA",
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
            "country": "USA",
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
            "country": "USA",
            "kind": "city",
            "pages": 50,
        }

    def test_create_muni_without_auth(self, client, webhook_data):
        """Test creating a new municipality without webhook secret"""
        url = reverse("munis:muni-webhook-update", kwargs={"subdomain": "webhookcity"})
        response = client.post(
            url, json.dumps(webhook_data), content_type="application/json"
        )

        assert response.status_code == 201
        data = response.json()
        assert data["action"] == "created"
        assert data["subdomain"] == "webhookcity"
        assert data["name"] == "Webhook City"
        assert Muni.objects.filter(subdomain="webhookcity").exists()

    def test_update_existing_muni_without_auth(self, client, webhook_data):
        """Test updating an existing municipality without webhook secret"""
        # Create existing muni
        existing_muni = Muni.objects.create(
            subdomain="webhookcity", name="Original City", state="NY", kind="city"
        )

        url = reverse("munis:muni-webhook-update", kwargs={"subdomain": "webhookcity"})
        webhook_data["name"] = "Updated Webhook City"
        response = client.post(
            url, json.dumps(webhook_data), content_type="application/json"
        )

        assert response.status_code == 200
        data = response.json()
        assert data["action"] == "updated"
        assert data["name"] == "Updated Webhook City"

        existing_muni.refresh_from_db()
        assert existing_muni.name == "Updated Webhook City"

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

    def test_webhook_with_put_method(self, client, webhook_data):
        """Test webhook endpoint accepts PUT requests"""
        url = reverse("munis:muni-webhook-update", kwargs={"subdomain": "putcity"})
        response = client.put(
            url, json.dumps(webhook_data), content_type="application/json"
        )

        assert response.status_code == 201
        data = response.json()
        assert data["action"] == "created"
        assert Muni.objects.filter(subdomain="putcity").exists()

    def test_webhook_invalid_json(self, client):
        """Test webhook with invalid JSON data"""
        url = reverse("munis:muni-webhook-update", kwargs={"subdomain": "invalidjson"})
        response = client.post(url, "invalid json", content_type="application/json")

        assert response.status_code == 400
        data = response.json()
        assert data["error"] == "Invalid JSON"

    def test_webhook_missing_required_name(self, client):
        """Test webhook with missing required name field"""
        url = reverse("munis:muni-webhook-update", kwargs={"subdomain": "noname"})
        data = {"state": "CA", "kind": "city"}
        response = client.post(url, json.dumps(data), content_type="application/json")

        assert response.status_code == 400
        response_data = response.json()
        assert response_data["error"] == "name field is required"

    def test_webhook_filters_invalid_fields(self, client):
        """Test webhook filters out invalid model fields"""
        url = reverse("munis:muni-webhook-update", kwargs={"subdomain": "filtered"})
        data = {
            "name": "Filtered City",
            "state": "CA",
            "kind": "city",
            "invalid_field": "should be ignored",
            "another_invalid": 123,
        }
        response = client.post(url, json.dumps(data), content_type="application/json")

        assert response.status_code == 201
        data = response.json()
        assert data["action"] == "created"
        assert "invalid_field" not in data
        assert "another_invalid" not in data
