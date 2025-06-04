import os
from typing import Any

from .base import *

DEBUG: bool = False  # type: ignore[no-redef]

ALLOWED_HOSTS: list[str] = os.environ.get("ALLOWED_HOSTS", "").split(",")  # type: ignore[no-redef]

DATABASES: dict[str, dict[str, Any]] = {  # type: ignore[no-redef]
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("DB_NAME"),
        "USER": os.environ.get("DB_USER"),
        "PASSWORD": os.environ.get("DB_PASSWORD"),
        "HOST": os.environ.get("DB_HOST", "localhost"),
        "PORT": os.environ.get("DB_PORT", "5432"),
    }
}

SECURE_BROWSER_XSS_FILTER: bool = True
SECURE_CONTENT_TYPE_NOSNIFF: bool = True
SECURE_HSTS_INCLUDE_SUBDOMAINS: bool = True
SECURE_HSTS_SECONDS: int = 31536000
SECURE_REDIRECT_EXEMPT: list[str] = []
SECURE_SSL_REDIRECT: bool = True
SESSION_COOKIE_SECURE: bool = True
CSRF_COOKIE_SECURE: bool = True
X_FRAME_OPTIONS: str = "DENY"

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
