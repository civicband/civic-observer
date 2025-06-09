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
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

EMAIL_BACKEND: str = "django.core.mail.backends.console.EmailBackend"  # type: ignore[no-redef]

# Override cookie domains for local development
SESSION_COOKIE_DOMAIN = None
CSRF_COOKIE_DOMAIN = None
