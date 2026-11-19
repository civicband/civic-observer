from django.apps import apps
from django.conf import settings


def test_watch_app_is_installed():
    assert apps.is_installed("watch")


def test_watch_settings_exist():
    assert hasattr(settings, "F5BOT_FEED_URL")
    assert hasattr(settings, "WATCH_DATA_DIR")
    assert settings.WATCH_INGEST_LOCK_SECONDS >= 60
