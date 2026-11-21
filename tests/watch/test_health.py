"""Tests for the stale-ingest health check, alert email, and command."""

from __future__ import annotations

from datetime import timedelta
from io import StringIO

import pytest
from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from tests.factories import IngestRunFactory
from watch.health import health_status, send_stale_alert

ALERT_CACHE_KEY = f"watch:stale-alert:{timezone.localdate().isoformat()}"


@pytest.fixture(autouse=True)
def isolated_cache(settings):
    """Use an in-process cache so alert keys never leak between tests."""
    settings.CACHES = {
        "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
    }
    cache.clear()
    yield
    cache.clear()


@pytest.mark.django_db
def test_health_reports_stale_and_sends_one_email(settings):
    settings.WATCH_ALERT_EMAIL = ["ops@example.com"]
    IngestRunFactory(finished_at=timezone.now() - timedelta(hours=8), new=10, error="")
    status = health_status()
    assert status["stale"] is True
    assert send_stale_alert(status) is True
    assert send_stale_alert(status) is False
    assert len(mail.outbox) == 1
    assert "f5bot.com" not in mail.outbox[0].body


@pytest.mark.django_db
def test_fresh_success_is_not_stale(settings):
    settings.WATCH_STALE_HOURS = 6
    run = IngestRunFactory(finished_at=timezone.now(), new=3, error="")

    status = health_status()

    assert status["last_success"] == run
    assert status["hours_since_success"] is not None
    assert status["hours_since_success"] < 1
    assert status["stale"] is False


@pytest.mark.django_db
def test_failed_run_does_not_count_as_last_success(settings):
    settings.WATCH_STALE_HOURS = 6
    IngestRunFactory(
        finished_at=timezone.now() - timedelta(hours=1), new=2, error="boom"
    )
    good = IngestRunFactory(
        finished_at=timezone.now() - timedelta(hours=8), new=4, error=""
    )

    status = health_status()

    assert status["last_success"] == good
    assert status["stale"] is True


@pytest.mark.django_db
def test_absent_success_is_stale(settings):
    settings.WATCH_STALE_HOURS = 6
    IngestRunFactory(finished_at=None, new=0, error="")

    status = health_status()

    assert status["last_success"] is None
    assert status["hours_since_success"] is None
    assert status["stale"] is True


@pytest.mark.django_db
def test_quiet_when_no_new_mentions_in_last_day():
    IngestRunFactory(
        started_at=timezone.now() - timedelta(days=2),
        finished_at=timezone.now() - timedelta(days=2),
        new=50,
        error="",
    )

    status = health_status()

    assert status["quiet"] is True


@pytest.mark.django_db
def test_not_quiet_with_recent_new_mentions():
    IngestRunFactory(
        started_at=timezone.now() - timedelta(hours=2),
        finished_at=timezone.now() - timedelta(hours=1),
        new=5,
        error="",
    )

    status = health_status()

    assert status["quiet"] is False


@pytest.mark.django_db
def test_alerts_today_sums_runs_started_today():
    now = timezone.now()
    IngestRunFactory(started_at=now - timedelta(hours=2), new=2, error="")
    IngestRunFactory(started_at=now - timedelta(hours=1), new=3, error="")
    IngestRunFactory(started_at=now - timedelta(days=1), new=100, error="")

    status = health_status()

    assert status["alerts_today"] == 5


@pytest.mark.django_db
def test_send_stale_alert_returns_false_without_recipients(settings):
    settings.WATCH_ALERT_EMAIL = []
    IngestRunFactory(finished_at=timezone.now() - timedelta(hours=8), error="")

    status = health_status()

    assert send_stale_alert(status) is False
    assert mail.outbox == []


@pytest.mark.django_db
def test_send_stale_alert_returns_false_when_not_stale(settings):
    settings.WATCH_ALERT_EMAIL = ["ops@example.com"]
    IngestRunFactory(finished_at=timezone.now(), new=4, error="")

    status = health_status()

    assert send_stale_alert(status) is False
    assert mail.outbox == []


@pytest.mark.django_db
def test_send_stale_alert_retries_after_failed_send(settings, monkeypatch):
    settings.WATCH_ALERT_EMAIL = ["ops@example.com"]
    IngestRunFactory(finished_at=timezone.now() - timedelta(hours=8), error="")
    status = health_status()

    monkeypatch.setattr("watch.health.send_mail", lambda *args, **kwargs: 0)
    assert send_stale_alert(status) is False
    assert cache.get(ALERT_CACHE_KEY) is None

    monkeypatch.undo()
    assert send_stale_alert(status) is True
    assert len(mail.outbox) == 1


@pytest.mark.django_db
def test_alert_email_body_has_run_counts_and_no_feed_url(settings):
    settings.WATCH_ALERT_EMAIL = ["ops@example.com"]
    settings.F5BOT_FEED_URL = "https://f5bot.com/secret-hash"
    IngestRunFactory(
        finished_at=timezone.now() - timedelta(hours=9),
        source="https://f5bot.com/secret-hash",
        pages=12,
        seen=30,
        new=4,
        error="",
    )

    status = health_status()

    assert send_stale_alert(status) is True
    message = mail.outbox[0]
    assert message.subject == "CivicBand Watch: ingest is stale"
    assert message.to == ["ops@example.com"]
    assert "/admin/watch/ingestrun/" in message.body
    assert "pages=12" in message.body
    assert "seen=30" in message.body
    assert "new=4" in message.body
    assert "f5bot.com" not in message.body
    assert "secret-hash" not in message.body


@pytest.mark.django_db
def test_check_command_exits_zero_when_healthy(settings):
    settings.WATCH_ALERT_EMAIL = []
    IngestRunFactory(finished_at=timezone.now(), new=2, error="")
    stdout = StringIO()

    call_command("check_watch_health", stdout=stdout)

    output = stdout.getvalue()
    assert "stale=False" in output
    assert "quiet=False" in output


@pytest.mark.django_db
def test_check_command_fails_when_stale(settings):
    settings.WATCH_ALERT_EMAIL = []
    IngestRunFactory(finished_at=timezone.now() - timedelta(hours=8), error="")

    with pytest.raises(CommandError):
        call_command("check_watch_health")


@pytest.mark.django_db
def test_check_command_fails_when_quiet(settings):
    settings.WATCH_ALERT_EMAIL = []
    IngestRunFactory(finished_at=timezone.now(), new=0, error="")

    with pytest.raises(CommandError):
        call_command("check_watch_health")


@pytest.mark.django_db
def test_check_command_sends_alert_email_when_stale(settings):
    settings.WATCH_ALERT_EMAIL = ["ops@example.com"]
    IngestRunFactory(finished_at=timezone.now() - timedelta(hours=8), error="")

    with pytest.raises(CommandError):
        call_command("check_watch_health")

    assert len(mail.outbox) == 1
    assert mail.outbox[0].subject == "CivicBand Watch: ingest is stale"
