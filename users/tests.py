import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import Client
from django.urls import reverse

from tests.factories import UserFactory

User = get_user_model()


@pytest.mark.django_db
class TestUserModel:
    def test_create_user_with_email(self):
        user = User.objects.create_user(  # type: ignore
            username="testuser", email="test@example.com", password="testpass123"
        )
        assert user.email == "test@example.com"
        assert user.username == "testuser"
        assert user.check_password("testpass123")
        assert user.is_active
        assert not user.is_staff
        assert not user.is_superuser

    def test_create_superuser(self):
        admin = User.objects.create_superuser(  # type: ignore
            username="admin", email="admin@example.com", password="adminpass123"
        )
        assert admin.email == "admin@example.com"
        assert admin.is_active
        assert admin.is_staff
        assert admin.is_superuser

    def test_email_is_unique(self):
        User.objects.create_user(  # type: ignore
            username="user1", email="test@example.com", password="pass123"
        )

        with pytest.raises(IntegrityError):
            User.objects.create_user(  # type: ignore
                username="user2", email="test@example.com", password="pass123"
            )

    def test_username_field_is_email(self):
        assert User.USERNAME_FIELD == "email"  # type: ignore

    def test_required_fields(self):
        assert "username" in User.REQUIRED_FIELDS

    def test_str_representation(self):
        user = UserFactory(email="test@example.com", username="testuser")
        assert str(user) == "test@example.com"

    def test_email_normalization(self):
        user = User.objects.create_user(  # type: ignore
            username="testuser", email="Test@EXAMPLE.COM", password="testpass123"
        )
        assert user.email == "Test@example.com"

    def test_user_factory(self):
        user = UserFactory()
        assert user.pk is not None
        assert user.email
        assert user.username
        assert user.check_password("defaultpass123")

    def test_admin_user_factory(self):
        from tests.factories import AdminUserFactory

        admin = AdminUserFactory()
        assert admin.is_staff
        assert admin.is_superuser


@pytest.mark.django_db
class TestDatasetteAuthView:
    def test_datasette_auth_authenticated_user(self):
        """Test that authenticated users get their id and email."""
        user = UserFactory(email="test@example.com")
        client = Client()
        client.force_login(user)  # type: ignore

        response = client.get(reverse("users:datasette_auth"))

        assert response.status_code == 200
        assert response["Content-Type"] == "application/json"
        data = response.json()
        assert data["id"] == user.id
        assert data["name"] == "test@example.com"

    def test_datasette_auth_unauthenticated_user(self):
        """Test that unauthenticated users are redirected to login."""
        client = Client()

        response = client.get(reverse("users:datasette_auth"))

        assert response.status_code == 302
        assert settings.LOGIN_URL in response["Location"]

    def test_datasette_auth_only_allows_get(self):
        """Test that only GET requests are allowed."""
        user = UserFactory()
        client = Client()
        client.force_login(user)  # type: ignore

        response = client.post(reverse("users:datasette_auth"))
        assert response.status_code == 405

        response = client.put(reverse("users:datasette_auth"))
        assert response.status_code == 405

        response = client.delete(reverse("users:datasette_auth"))
        assert response.status_code == 405
