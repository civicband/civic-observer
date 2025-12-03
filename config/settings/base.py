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
    "stagedoor",
    "django_rq",
]

LOCAL_APPS: list[str] = [
    "users",
    "municipalities",
    "searches",
    "meetings",
    "notebooks",
    "apikeys",
    "notifications",
    "analytics",
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
                "analytics.context_processors.umami_context",
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

# PgBouncer compatibility: Disable server-side cursors for transaction pooling mode
# When using pgBouncer in transaction mode, server-side cursors cannot persist
# across transactions, so Django must use client-side cursors instead
DISABLE_SERVER_SIDE_CURSORS: bool = True

AUTHENTICATION_BACKENDS = (
    "stagedoor.backends.EmailTokenBackend",
    "django.contrib.auth.backends.ModelBackend",
)

STAGEDOOR_LOGIN_REDIRECT = "/searches/"
LOGIN_URL = "/login/"
LOGOUT_REDIRECT_URL = "/"
STAGEDOOR_REQUIRE_ADMIN_APPROVAL = True
STAGEDOOR_SITE_NAME = "CivicObserver"

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

# Django-RQ configuration
REDIS_URL = env.str("REDIS_URL", "redis://localhost:6379/0")
RQ_QUEUES: dict[str, dict[str, Any]] = {
    "default": {
        "URL": REDIS_URL,
        "DEFAULT_TIMEOUT": 600,  # 6 minutes for backfill tasks
    },
}
RQ_SHOW_ADMIN_LINK = True

# API Key Validation
CORKBOARD_SERVICE_SECRET = env.str("CORKBOARD_SERVICE_SECRET", "")

# Notification Channel Settings
DISCORD_BOT_TOKEN = env.str("DISCORD_BOT_TOKEN", "")
BLUESKY_BOT_HANDLE = env.str("BLUESKY_BOT_HANDLE", "")
BLUESKY_BOT_PASSWORD = env.str("BLUESKY_BOT_PASSWORD", "")
MASTODON_ACCESS_TOKEN = env.str("MASTODON_ACCESS_TOKEN", "")
MASTODON_INSTANCE_URL = env.str("MASTODON_INSTANCE_URL", "https://mastodon.social")

# Umami Analytics
UMAMI_ENABLED: bool = False
UMAMI_WEBSITE_ID: str = "522b42fb-2e46-4ba3-9803-4e17c7824958"
UMAMI_SCRIPT_URL: str = "https://analytics.civic.band/sunshine"
