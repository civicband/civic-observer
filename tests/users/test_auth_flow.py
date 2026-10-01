"""End-to-end tests for the stagedoor email authentication flow."""

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.urls import reverse
from stagedoor import __version__ as stagedoor_version
from stagedoor.models import AuthToken, Email

User = get_user_model()

pytestmark = pytest.mark.django_db

EMAIL = "newuser@example.com"


def _stagedoor_version() -> tuple[int, ...]:
    parts = []
    for part in stagedoor_version.split(".")[:3]:
        try:
            parts.append(int(part))
        except ValueError:
            break
    return tuple(parts)


def _request_login(client, email: str = EMAIL):
    return client.post(reverse("stagedoor:login"), {"email": email})


class TestEmailAuthFlow:
    def test_login_creates_approved_token_and_redirects_to_token_post(self, client):
        response = _request_login(client)

        assert response.status_code == 302
        assert response.url == reverse("stagedoor:token-post")
        email = Email.objects.get(email=EMAIL)
        token = AuthToken.objects.get(email=email)
        assert token.approved is True

    def test_login_sends_verification_email(self, client, settings):
        settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

        _request_login(client)

        assert len(mail.outbox) == 1
        assert EMAIL in mail.outbox[0].to

    def test_submitting_code_creates_user_and_logs_in(self, client):
        _request_login(client)
        token = AuthToken.objects.get(email__email=EMAIL)

        response = client.post(reverse("stagedoor:token-post"), {"token": token.token})

        assert response.status_code == 302
        assert response.url == "/searches/"
        user = User.objects.get(email=EMAIL)
        assert client.session.get("_auth_user_id") == str(user.pk)

    def test_clicking_link_creates_user_and_logs_in(self, client):
        _request_login(client)
        token = AuthToken.objects.get(email__email=EMAIL)

        response = client.get(
            reverse("stagedoor:token-login", kwargs={"token": token.token})
        )

        assert response.status_code == 302
        assert response.url == "/searches/"
        assert User.objects.filter(email=EMAIL).exists()
        assert client.session.get("_auth_user_id")

    def test_token_is_single_use(self, client):
        _request_login(client)
        token = AuthToken.objects.get(email__email=EMAIL)
        login_url = reverse("stagedoor:token-login", kwargs={"token": token.token})

        assert client.get(login_url).status_code == 302
        assert not AuthToken.objects.filter(pk=token.pk).exists()

        from django.test import Client

        second_client = Client()
        response = second_client.get(login_url)
        assert response.status_code == 302
        assert response.url.startswith("/login/")
        assert second_client.session.get("_auth_user_id") is None

    @pytest.mark.skipif(
        _stagedoor_version() < (0, 3, 2),
        reason="requires django-stagedoor>=0.3.2 (unapproved token enforcement)",
    )
    def test_unapproved_token_is_rejected(self, client):
        email = Email.objects.create(email=EMAIL)
        token = AuthToken.objects.create(
            email=email, token="unapprovedtoken", approved=False
        )

        response = client.get(
            reverse("stagedoor:token-login", kwargs={"token": token.token})
        )

        assert response.status_code == 302
        assert response.url.startswith("/login/")
        assert client.session.get("_auth_user_id") is None
        assert not User.objects.filter(email=EMAIL).exists()

    def test_protected_page_requires_login(self, client):
        response = client.get(reverse("notebooks:notebook-list"))

        assert response.status_code == 302
        assert response.url.startswith(reverse("login"))


class TestAuthNav:
    def test_nav_shows_login_when_anonymous(self, client):
        response = client.get(reverse("homepage"))

        assert b"Log in" in response.content

    def test_nav_shows_logout_when_authenticated(self, authenticated_client):
        response = authenticated_client.get(reverse("homepage"))

        assert b"Log out" in response.content
