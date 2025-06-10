from pathlib import Path
from typing import Any

from environs import env

BASE_DIR: Path = Path(__file__).resolve().parent.parent.parent

SECRET_KEY: str = env.str(
    "SECRET_KEY", "django-insecure-b-epto38!pfzefkm75o8^mi88b*=lu+r$bw^_op6frmhj$zo0m"
)

DEBUG: bool = env.bool("DJANGO_DEBUG", default=False)

ALLOWED_HOSTS: list[str] = env.list("ALLOWED_HOSTS", ["localhost", "127.0.0.1"])

DJANGO_APPS: list[str] = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

THIRD_PARTY_APPS: list[str] = [
    "django_tailwind_cli",
    "anymail",
    "widget_tweaks",
]

LOCAL_APPS: list[str] = [
    "users",
    "municipalities",
    "searches",
]

INSTALLED_APPS: list[str] = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE: list[str] = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF: str = "config.urls"

TEMPLATES: list[dict[str, Any]] = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION: str = "config.wsgi.application"

DATABASES: dict[str, dict[str, Any]] = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

AUTH_PASSWORD_VALIDATORS: list[dict[str, str]] = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

LANGUAGE_CODE: str = "en-us"

TIME_ZONE: str = "UTC"

USE_I18N: bool = True

USE_TZ: bool = True

STATIC_URL: str = "/static/"
STATIC_ROOT: Path = BASE_DIR / "static"

# Only include static dir if it exists
_STATIC_DIR = BASE_DIR / "frontend"
STATICFILES_DIRS: list[Path] = [_STATIC_DIR] if _STATIC_DIR.exists() else []

STATICFILES_STORAGE: str = "whitenoise.storage.CompressedManifestStaticFilesStorage"

MEDIA_URL = "/media/"
MEDIA_ROOT: Path = BASE_DIR / "media"

DEFAULT_AUTO_FIELD: str = "django.db.models.BigAutoField"

AUTH_USER_MODEL: str = "users.User"

# Email configuration
EMAIL_BACKEND = "anymail.backends.postmark.EmailBackend"
ANYMAIL = {
    "POSTMARK_SERVER_TOKEN": env.str("POSTMARK_SERVER_TOKEN", ""),
}

# Default email settings
DEFAULT_FROM_EMAIL = "Civic Observer <noreply@civic.observer>"
SERVER_EMAIL = "Civic Observer <server@civic.observer>"
