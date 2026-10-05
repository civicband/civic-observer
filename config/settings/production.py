from typing import Any

import sentry_sdk
from django.core.exceptions import ImproperlyConfigured
from environs import env
from sentry_sdk.integrations.rq import RqIntegration
from sentry_sdk.types import Event, Hint

from .base import *

# Production must never fall back to the development default. Re-reading
# without a default makes a missing SECRET_KEY a hard startup error, and the
# equality check catches an explicit misconfiguration.
SECRET_KEY: str = env.str("SECRET_KEY")  # type: ignore[no-redef]
if SECRET_KEY == INSECURE_SECRET_KEY:
    raise ImproperlyConfigured(
        "SECRET_KEY must be set to a unique, secret value in production."
    )


def _get_exception_name(exc: BaseException | None) -> str:
    """Get fully qualified exception name for fingerprinting."""
    if exc is None:
        return ""
    exc_type = type(exc)
    module = getattr(exc_type, "__module__", "")
    name = getattr(exc_type, "__name__", "")
    if module:
        return f"{module}.{name}"
    return name


_SENSITIVE_HEADERS = frozenset(
    {
        "authorization",
        "cookie",
        "set-cookie",
        "x-service-secret",
        "x-csrf-token",
    }
)


def _scrub_event(event: Event) -> Event:
    """Remove credentials from event data before it leaves the process."""
    request = event.get("request")
    if isinstance(request, dict):
        request.pop("data", None)
        headers = request.get("headers")
        if isinstance(headers, dict):
            for header in list(headers):
                if header.lower() in _SENSITIVE_HEADERS:
                    del headers[header]
    return event


def sentry_before_send(event: Event, hint: Hint) -> Event | None:
    """
    Custom fingerprinting to group related errors together.

    Groups infrastructure errors (database, redis, HTTP) by type rather than
    by stack trace location, preventing alert fatigue from infrastructure issues.
    """
    event = _scrub_event(event)

    if "exc_info" not in hint:
        return event

    exc_info = hint.get("exc_info")
    if not exc_info or len(exc_info) < 2:
        return event

    exc = exc_info[1]
    exc_name = _get_exception_name(exc)

    # Database connection errors (Django/psycopg)
    if "OperationalError" in exc_name and "django.db" in exc_name:
        event["fingerprint"] = ["database-connection-error"]
        return event

    if "psycopg" in exc_name:
        event["fingerprint"] = ["database-error"]
        return event

    # Redis connection errors
    if "redis" in exc_name.lower():
        event["fingerprint"] = ["redis-connection-error"]
        return event

    # HTTP client errors (httpx used for civic.band API)
    if "httpx" in exc_name.lower():
        # Group by exception type (ConnectError, TimeoutError, etc.)
        simple_name = exc_name.split(".")[-1]
        event["fingerprint"] = ["httpx-error", simple_name]
        return event

    # BackfillError - group by municipality if present in message
    if "BackfillError" in exc_name:
        # Include default grouping but add category
        event["fingerprint"] = ["{{ default }}", "backfill-error"]
        return event

    return event


sentry_sdk.init(
    dsn=env.str("SENTRY_DSN", default=""),
    # Keep credentials and user PII out of Sentry; scrub_event is defense in depth.
    send_default_pii=False,
    max_request_body_size="never",
    traces_sample_rate=0,
    # Custom error grouping via fingerprinting
    before_send=sentry_before_send,
    # Report failed django-rq jobs; Django itself is still auto-detected.
    integrations=[RqIntegration()],
    # Do not attach stack locals, which can contain secrets.
    include_local_variables=False,
    # Environment tag for filtering
    environment=env.str("SENTRY_ENVIRONMENT", default="production"),
    # Release tracking (use VERSION env var if set)
    release=env.str("VERSION", default=None),
)

DEBUG: bool = False  # type: ignore[no-redef]

ALLOWED_HOSTS: list[str] = [  # type: ignore[no-redef]
    "civic.observer",
    "*.civic.observer",
    "localhost",  # For health checks
    "127.0.0.1",
]

DATABASES: dict[str, dict[str, Any]] = {  # type: ignore[no-redef]
    "default": {
        **env.dj_db_url("DATABASE_URL"),
        "CONN_MAX_AGE": 600,  # Keep connections alive for 10 minutes
        "CONN_HEALTH_CHECKS": True,  # Validate connections before use
        # Note: connect_timeout not supported by pgBouncer in transaction mode
    }
}

# Cookie settings for civic.observer domain
# SESSION_COOKIE_DOMAIN: str | None = ".civic.observer"
# CSRF_COOKIE_DOMAIN: str | None = ".civic.observer"
CSRF_TRUSTED_ORIGINS: list[str] = [
    "https://civic.observer",
    "https://*.civic.observer",
]  # type: ignore[no-redef]

# Transport and session hardening. Caddy terminates TLS and forwards the
# original scheme, so SECURE_PROXY_SSL_HEADER lets Django detect HTTPS and
# redirect plaintext requests. /health/ is exempted so the in-network Docker
# healthcheck (plain HTTP on localhost) keeps working.
SECURE_PROXY_SSL_HEADER: tuple[str, str] = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT: bool = True
SECURE_REDIRECT_EXEMPT: list[str] = [r"^health/$"]
SESSION_COOKIE_SECURE: bool = True
CSRF_COOKIE_SECURE: bool = True
SECURE_HSTS_SECONDS: int = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS: bool = True
SECURE_HSTS_PRELOAD: bool = True

ANYMAIL = {
    "POSTMARK_SERVER_TOKEN": env.str("POSTMARK_SERVER_TOKEN", ""),
}
EMAIL_USE_TLS = True

LOGGING: dict[str, Any] = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {
            "format": "%(asctime)s %(levelname)s %(name)s %(message)s",
        },
    },
    "handlers": {
        "console": {
            "level": "INFO",
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stdout",
            "formatter": "standard",
        },
    },
    # Send everything to stdout so the container log shipper can collect app
    # logs; a file inside the container is invisible to Vector.
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
}

# Django-RQ for production
REDIS_URL = env.str("REDIS_URL", "redis://redis:6379/0")  # type: ignore[no-redef]
RQ_QUEUES: dict[str, dict[str, Any]] = {  # type: ignore[no-redef]
    "default": {
        "URL": REDIS_URL,
        "DEFAULT_TIMEOUT": 360,
        "ASYNC": True,
    },
}

# Umami Analytics - enabled in production
UMAMI_ENABLED: bool = True  # type: ignore[no-redef]
