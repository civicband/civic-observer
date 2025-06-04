from typing import Any

from .base import *

DEBUG: bool = True  # type: ignore[no-redef]

ALLOWED_HOSTS: list[str] = ["localhost", "127.0.0.1", "0.0.0.0"]  # type: ignore[no-redef]

INSTALLED_APPS += [
    "django_extensions",
]

DATABASES: dict[str, dict[str, Any]] = {  # type: ignore[no-redef]
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

EMAIL_BACKEND: str = "django.core.mail.backends.console.EmailBackend"

INTERNAL_IPS: list[str] = [
    "127.0.0.1",
]
