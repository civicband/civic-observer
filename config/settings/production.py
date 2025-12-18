from typing import Any

import sentry_sdk
from environs import env
from sentry_sdk.types import Event, Hint

from .base import *


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


def sentry_before_send(event: Event, hint: Hint) -> Event | None:
    """
    Custom fingerprinting to group related errors together.

    Groups infrastructure errors (database, redis, HTTP) by type rather than
    by stack trace location, preventing alert fatigue from infrastructure issues.
    """
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
    # Add data like request headers and IP for users;
    # see https://docs.sentry.io/platforms/python/data-management/data-collected/ for more info
    send_default_pii=True,
    max_request_body_size="always",
    traces_sample_rate=0,
    # Custom error grouping via fingerprinting
    before_send=sentry_before_send,
    # Django-specific integrations (auto-enabled but explicit for clarity)
    integrations=[],  # Let sentry auto-detect Django
    # Attach stack locals for better debugging
    include_local_variables=True,
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
    "http://civic.observer",
    "https://*.civic.observer",
]  # type: ignore[no-redef]

# SECURE_BROWSER_XSS_FILTER: bool = True
# SECURE_CONTENT_TYPE_NOSNIFF: bool = True
# SECURE_HSTS_INCLUDE_SUBDOMAINS: bool = True
# SECURE_HSTS_SECONDS: int = 31536000
# SECURE_REDIRECT_EXEMPT: list[str] = []
# SECURE_SSL_REDIRECT: bool = True
# SESSION_COOKIE_SECURE: bool = True
CSRF_COOKIE_SECURE: bool = True
# X_FRAME_OPTIONS: str = "DENY"

ANYMAIL = {
    "POSTMARK_SERVER_TOKEN": env.str("POSTMARK_SERVER_TOKEN", ""),
}
EMAIL_USE_TLS = True

LOGGING: dict[str, Any] = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "file": {
            "level": "INFO",
            "class": "logging.FileHandler",
            "filename": BASE_DIR / "django.log",
        },
    },
    "loggers": {
        "django": {
            "handlers": ["file"],
            "level": "INFO",
            "propagate": True,
        },
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
