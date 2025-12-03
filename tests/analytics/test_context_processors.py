import pytest
from django.test import RequestFactory

from analytics.context_processors import umami_context


@pytest.fixture
def request_factory():
    return RequestFactory()


@pytest.fixture
def anonymous_request(request_factory):
    from django.contrib.auth.models import AnonymousUser

    request = request_factory.get("/")
    request.user = AnonymousUser()
    return request


class TestUmamiContext:
    def test_returns_expected_keys(self, anonymous_request):
        """Context processor returns all required keys."""
        result = umami_context(anonymous_request)

        assert "umami_enabled" in result
        assert "umami_opted_out" in result
        assert "umami_website_id" in result
        assert "umami_script_url" in result

    def test_disabled_by_default(self, anonymous_request, settings):
        """Umami is disabled by default."""
        settings.UMAMI_ENABLED = False

        result = umami_context(anonymous_request)

        assert result["umami_enabled"] is False

    def test_enabled_when_setting_true(self, anonymous_request, settings):
        """Umami is enabled when UMAMI_ENABLED is True."""
        settings.UMAMI_ENABLED = True

        result = umami_context(anonymous_request)

        assert result["umami_enabled"] is True

    def test_respects_dnt_header(self, request_factory, settings):
        """Umami is disabled when DNT header is set."""
        from django.contrib.auth.models import AnonymousUser

        settings.UMAMI_ENABLED = True
        request = request_factory.get("/", HTTP_DNT="1")
        request.user = AnonymousUser()

        result = umami_context(request)

        assert result["umami_enabled"] is False

    def test_opted_out_for_anonymous_user(self, anonymous_request):
        """Anonymous users are not opted out."""
        result = umami_context(anonymous_request)

        assert result["umami_opted_out"] is False

    @pytest.mark.django_db
    def test_opted_out_when_user_has_flag(self, request_factory, user):
        """User with analytics_opt_out=True is opted out."""
        user.analytics_opt_out = True
        user.save()

        request = request_factory.get("/")
        request.user = user

        result = umami_context(request)

        assert result["umami_opted_out"] is True

    @pytest.mark.django_db
    def test_not_opted_out_when_user_flag_false(self, request_factory, user):
        """User with analytics_opt_out=False is not opted out."""
        user.analytics_opt_out = False
        user.save()

        request = request_factory.get("/")
        request.user = user

        result = umami_context(request)

        assert result["umami_opted_out"] is False

    def test_returns_website_id(self, anonymous_request, settings):
        """Context includes website ID from settings."""
        settings.UMAMI_WEBSITE_ID = "test-website-id"

        result = umami_context(anonymous_request)

        assert result["umami_website_id"] == "test-website-id"

    def test_returns_script_url(self, anonymous_request, settings):
        """Context includes script URL from settings."""
        settings.UMAMI_SCRIPT_URL = "https://example.com/script.js"

        result = umami_context(anonymous_request)

        assert result["umami_script_url"] == "https://example.com/script.js"
