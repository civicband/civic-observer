"""Tests for user views including login page."""

import pytest
from django.urls import reverse


@pytest.mark.django_db
class TestLoginView:
    """Tests for the login view."""

    def test_login_page_returns_200_for_get(self, client):
        """
        Test that GET /login/ returns 200.

        This is a regression test to ensure the login page is accessible.
        Previously, links incorrectly pointed to stagedoor:login (POST-only),
        causing 405 errors when users clicked login links.
        """
        response = client.get(reverse("login"))
        assert response.status_code == 200

    def test_login_page_renders_form(self, client):
        """Test that the login page renders a form with email field."""
        response = client.get(reverse("login"))
        assert response.status_code == 200
        assert b"email" in response.content.lower()
        assert b"login" in response.content.lower()

    def test_login_form_posts_to_auth_login(self, client):
        """Test that the login form posts to /auth/login."""
        response = client.get(reverse("login"))
        assert response.status_code == 200
        # The form should post to /auth/login
        assert b'action="/auth/login"' in response.content
