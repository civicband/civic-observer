from typing import Any

from environs import env

from .base import *

DEBUG: bool = True  # type: ignore[no-redef]

DATABASES: dict[str, dict[str, Any]] = {  # type: ignore[no-redef]
    "default": env.dj_db_url("DATABASE_URL"),
}

# SECURE_BROWSER_XSS_FILTER: bool = True
# SECURE_CONTENT_TYPE_NOSNIFF: bool = True
# SECURE_HSTS_INCLUDE_SUBDOMAINS: bool = True
# SECURE_HSTS_SECONDS: int = 31536000
# SECURE_REDIRECT_EXEMPT: list[str] = []
# SECURE_SSL_REDIRECT: bool = True
# SESSION_COOKIE_SECURE: bool = True
# CSRF_COOKIE_SECURE: bool = True
# X_FRAME_OPTIONS: str = "DENY"

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
