from typing import Any

import sentry_sdk
from environs import env

from .base import *

sentry_sdk.init(
    dsn=env.str("SENTRY_DSN", default=""),
    # Add data like request headers and IP for users;
    # see https://docs.sentry.io/platforms/python/data-management/data-collected/ for more info
    send_default_pii=True,
    max_request_body_size="always",
    traces_sample_rate=0,
)

DEBUG: bool = False  # type: ignore[no-redef]

ALLOWED_HOSTS: list[str] = [  # type: ignore[no-redef]
    "civic.observer",
    "*.civic.observer",
]

DATABASES: dict[str, dict[str, Any]] = {  # type: ignore[no-redef]
    "default": env.dj_db_url("DATABASE_URL"),
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
