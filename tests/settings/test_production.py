"""Tests for production settings hardening.

These load ``config/settings/production.py`` directly from disk so they can
exercise its import-time behavior (required env vars, fail-fast checks)
without depending on the process-wide ``DJANGO_SETTINGS_MODULE``.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from environs import EnvError

PRODUCTION_SETTINGS_PATH = (
    Path(settings.BASE_DIR) / "config" / "settings" / "production.py"
)

INSECURE_DEFAULT = "django-insecure-b-epto38!pfzefkm75o8^mi88b*=lu+r$bw^_op6frmhj$zo0m"
VALID_SECRET_KEY = "prod-secret-key-" + "a" * 48
VALID_DATABASE_URL = "postgres://civic:secret@db.example.com:5432/civicobserver"


def _load_production():
    """Execute production.py as a fresh module without polluting sys.modules."""
    spec = importlib.util.spec_from_file_location(
        "config.settings.production", PRODUCTION_SETTINGS_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def production_env(monkeypatch):
    """Provide the minimum env production needs, minus SECRET_KEY."""
    monkeypatch.setenv("DATABASE_URL", VALID_DATABASE_URL)
    monkeypatch.delenv("SECRET_KEY", raising=False)
    return monkeypatch


def test_production_requires_secret_key(production_env):
    with patch("sentry_sdk.init"):
        with pytest.raises(EnvError):
            _load_production()


def test_production_rejects_insecure_default_secret_key(production_env):
    production_env.setenv("SECRET_KEY", INSECURE_DEFAULT)
    with patch("sentry_sdk.init"):
        with pytest.raises(ImproperlyConfigured):
            _load_production()


def test_production_accepts_valid_secret_key(production_env):
    production_env.setenv("SECRET_KEY", VALID_SECRET_KEY)
    with patch("sentry_sdk.init"):
        production = _load_production()
    assert production.SECRET_KEY == VALID_SECRET_KEY


def test_production_enables_transport_and_session_hardening(production_env):
    production_env.setenv("SECRET_KEY", VALID_SECRET_KEY)
    with patch("sentry_sdk.init"):
        production = _load_production()

    assert production.SECURE_SSL_REDIRECT is True
    assert production.SECURE_REDIRECT_EXEMPT == [r"^health/$"]
    assert production.SESSION_COOKIE_SECURE is True
    assert production.CSRF_COOKIE_SECURE is True
    assert production.SECURE_HSTS_SECONDS == 31536000
    assert production.SECURE_HSTS_INCLUDE_SUBDOMAINS is True
    assert production.SECURE_HSTS_PRELOAD is True
    assert production.SECURE_PROXY_SSL_HEADER == ("HTTP_X_FORWARDED_PROTO", "https")


def test_production_csrf_trusted_origins_are_https_only(production_env):
    production_env.setenv("SECRET_KEY", VALID_SECRET_KEY)
    with patch("sentry_sdk.init"):
        production = _load_production()

    assert "https://civic.observer" in production.CSRF_TRUSTED_ORIGINS
    assert all(
        origin.startswith("https://") for origin in production.CSRF_TRUSTED_ORIGINS
    )


def test_production_sentry_minimizes_pii(production_env):
    production_env.setenv("SECRET_KEY", VALID_SECRET_KEY)
    with patch("sentry_sdk.init") as init:
        _load_production()

    kwargs = init.call_args.kwargs
    assert kwargs["send_default_pii"] is False
    assert kwargs["max_request_body_size"] == "never"
    assert kwargs["include_local_variables"] is False


def test_production_reports_failed_rq_jobs(production_env):
    production_env.setenv("SECRET_KEY", VALID_SECRET_KEY)
    with patch("sentry_sdk.init") as init:
        _load_production()

    integrations = init.call_args.kwargs["integrations"]
    assert any(type(i).__name__ == "RqIntegration" for i in integrations)


def test_sentry_before_send_scrubs_request_credentials(production_env):
    production_env.setenv("SECRET_KEY", VALID_SECRET_KEY)
    with patch("sentry_sdk.init"):
        production = _load_production()

    event = {
        "request": {
            "data": {"password": "hunter2"},
            "headers": {
                "Authorization": "Bearer secret",
                "cookie": "sessionid=abc",
                "X-Service-Secret": "shared-secret",
                "Accept": "text/html",
            },
        }
    }

    result = production.sentry_before_send(event, {})

    assert result is not None
    request = result["request"]
    assert "data" not in request
    assert "Authorization" not in request["headers"]
    assert "cookie" not in request["headers"]
    assert "X-Service-Secret" not in request["headers"]
    assert request["headers"]["Accept"] == "text/html"
