from typing import Any

from environs import env

from .base import *

DEBUG: bool = True  # type: ignore[no-redef]

ALLOWED_HOSTS: list[str] = env.list(  # type: ignore[no-redef]
    "ALLOWED_HOSTS", ["localhost", "127.0.0.1", "0.0.0.0", "testserver"]
)

INSTALLED_APPS += [
    "django_extensions",
]

DATABASES: dict[str, dict[str, Any]] = {  # type: ignore[no-redef]
    "default": {
        **env.dj_db_url("DATABASE_URL", default="postgres://postgres@db/postgres"),
        "CONN_MAX_AGE": 600,  # Keep connections alive for 10 minutes
        "CONN_HEALTH_CHECKS": True,  # Validate connections before use
        # Note: connect_timeout not supported by pgBouncer in transaction mode
    }
}

EMAIL_BACKEND: str = "django.core.mail.backends.console.EmailBackend"  # type: ignore[no-redef]

# Override cookie domains for local development
SESSION_COOKIE_DOMAIN = None
CSRF_COOKIE_DOMAIN = None

# Django-RQ for development (use docker service name if in docker)
REDIS_URL = env.str("REDIS_URL", "redis://redis:6379/0")  # type: ignore[no-redef]
RQ_QUEUES: dict[str, dict[str, Any]] = {  # type: ignore[no-redef]
    "default": {
        "URL": REDIS_URL,
        "DEFAULT_TIMEOUT": 360,
        "ASYNC": True,  # Run tasks asynchronously in development
    },
}
