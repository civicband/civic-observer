"""Tests for the stale-ingest health check, alert email, and command."""

from __future__ import annotations

import logging
from datetime import timedelta
from io import StringIO
from unittest.mock import Mock

import pytest
from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from tests.factories import IngestRunFactory
from watch.health import health_status, send_quiet_alert, send_stale_alert

ALERT_CACHE_KEY = f"watch:stale-alert:{timezone.localdate().isoformat()}"
QUIET_CACHE_KEY = f"watch:quiet-alert:{timezone.localdate().isoformat()}"


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
    # Noon local keeps "today", "yesterday", and "next hour" unambiguous.
    now = timezone.localtime(timezone.now()).replace(
        hour=12, minute=0, second=0, microsecond=0
    )
    IngestRunFactory(started_at=now - timedelta(hours=2), new=2, error="")
    IngestRunFactory(started_at=now - timedelta(hours=1), new=3, error="")
    # Within the quiet window but started yesterday: not counted today.
    IngestRunFactory(started_at=now - timedelta(hours=23), new=11, error="")
    IngestRunFactory(started_at=now - timedelta(days=1), new=100, error="")
    # Future-dated/clock-skew row must not count today.
    IngestRunFactory(started_at=now + timedelta(hours=1), new=7, error="")

    status = health_status(now=now)

    assert status["alerts_today"] == 5
    assert status["quiet"] is False


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
def test_send_stale_alert_logs_and_releases_key_when_send_raises(
    settings, monkeypatch, caplog
):
    settings.WATCH_ALERT_EMAIL = ["ops@example.com"]
    IngestRunFactory(finished_at=timezone.now() - timedelta(hours=8), error="")
    status = health_status()

    def boom(*args, **kwargs):
        raise RuntimeError("smtp down")

    monkeypatch.setattr("watch.health.send_mail", boom)
    with caplog.at_level(logging.ERROR, logger="watch.health"):
        assert send_stale_alert(status) is False

    assert cache.get(ALERT_CACHE_KEY) is None
    assert "stale alert send failed" in caplog.text
    assert mail.outbox == []


@pytest.mark.django_db
def test_send_stale_alert_returns_false_when_cache_add_raises(
    settings, monkeypatch, caplog
):
    settings.WATCH_ALERT_EMAIL = ["ops@example.com"]
    IngestRunFactory(finished_at=timezone.now() - timedelta(hours=8), error="")
    status = health_status()
    broken_cache = Mock()
    broken_cache.add.side_effect = RuntimeError("redis down")
    monkeypatch.setattr("watch.health.cache", broken_cache)

    with caplog.at_level(logging.ERROR, logger="watch.health"):
        assert send_stale_alert(status) is False

    assert "stale alert cache add failed" in caplog.text
    assert mail.outbox == []


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
def test_stale_alert_formats_ingest_run_finished_at(settings):
    settings.WATCH_ALERT_EMAIL = ["ops@example.com"]
    finished = timezone.now().replace(microsecond=0) - timedelta(hours=8)
    IngestRunFactory(finished_at=finished, new=1, error="")

    status = health_status()

    assert send_stale_alert(status) is True
    expected = timezone.localtime(finished).strftime("%Y-%m-%d %H:%M %Z")
    body = mail.outbox[0].body
    assert expected in body
    assert "never" not in body


@pytest.mark.django_db
def test_stale_alert_without_success_renders_never(settings):
    settings.WATCH_ALERT_EMAIL = ["ops@example.com"]
    IngestRunFactory(finished_at=None, new=0, error="")

    status = health_status()

    assert send_stale_alert(status) is True
    assert "Last successful ingest: never" in mail.outbox[0].body


@pytest.mark.django_db
def test_check_command_is_silent_when_healthy(settings):
    settings.WATCH_ALERT_EMAIL = []
    IngestRunFactory(finished_at=timezone.now(), new=2, error="")
    stdout = StringIO()

    call_command("check_watch_health", stdout=stdout)

    assert stdout.getvalue() == ""


@pytest.mark.django_db
def test_check_command_prints_status_when_stale(settings):
    settings.WATCH_ALERT_EMAIL = []
    IngestRunFactory(finished_at=timezone.now() - timedelta(hours=8), error="")
    stdout = StringIO()

    with pytest.raises(CommandError):
        call_command("check_watch_health", stdout=stdout)

    output = stdout.getvalue()
    assert "stale=True" in output
    assert "alert=stale" in output


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
def test_quiet_day_sends_one_quiet_email_and_exits_nonzero(settings):
    settings.WATCH_ALERT_EMAIL = ["ops@example.com"]
    settings.F5BOT_FEED_URL = "https://f5bot.com/secret-hash"
    finished = timezone.now().replace(microsecond=0) - timedelta(hours=1)
    IngestRunFactory(finished_at=finished, new=0, error="")
    stdout = StringIO()

    with pytest.raises(CommandError):
        call_command("check_watch_health", stdout=stdout)

    assert len(mail.outbox) == 1
    message = mail.outbox[0]
    assert message.subject == "CivicBand Watch: no new alerts"
    assert message.to == ["ops@example.com"]
    expected = timezone.localtime(finished).strftime("%Y-%m-%d %H:%M %Z")
    assert f"Last successful ingest: {expected}" in message.body
    assert "Hours since last success:" in message.body
    assert "New mentions today: 0" in message.body
    assert "/admin/watch/ingestrun/" in message.body
    assert "f5bot.com" not in message.body
    assert "secret-hash" not in message.body
    assert "quiet=True" in stdout.getvalue()
    assert "alert=quiet" in stdout.getvalue()


@pytest.mark.django_db
def test_stale_and_quiet_sends_only_stale_email(settings):
    settings.WATCH_ALERT_EMAIL = ["ops@example.com"]
    IngestRunFactory(finished_at=timezone.now() - timedelta(hours=8), new=0, error="")

    with pytest.raises(CommandError):
        call_command("check_watch_health")

    assert len(mail.outbox) == 1
    assert mail.outbox[0].subject == "CivicBand Watch: ingest is stale"


@pytest.mark.django_db
def test_second_quiet_run_same_day_does_not_send(settings):
    settings.WATCH_ALERT_EMAIL = ["ops@example.com"]
    IngestRunFactory(finished_at=timezone.now(), new=0, error="")

    for _ in range(2):
        with pytest.raises(CommandError):
            call_command("check_watch_health")

    assert len(mail.outbox) == 1
    assert mail.outbox[0].subject == "CivicBand Watch: no new alerts"
    assert cache.get(QUIET_CACHE_KEY) is not None


@pytest.mark.django_db
def test_send_quiet_alert_returns_false_when_not_quiet(settings):
    settings.WATCH_ALERT_EMAIL = ["ops@example.com"]
    IngestRunFactory(finished_at=timezone.now(), new=4, error="")

    status = health_status()

    assert send_quiet_alert(status) is False
    assert mail.outbox == []


@pytest.mark.django_db
def test_send_quiet_alert_returns_false_when_stale(settings):
    settings.WATCH_ALERT_EMAIL = ["ops@example.com"]
    IngestRunFactory(finished_at=timezone.now() - timedelta(hours=8), new=0, error="")

    status = health_status()

    assert status["stale"] is True
    assert status["quiet"] is True
    assert send_quiet_alert(status) is False
    assert mail.outbox == []


@pytest.mark.django_db
def test_send_quiet_alert_retries_after_failed_send(settings, monkeypatch):
    settings.WATCH_ALERT_EMAIL = ["ops@example.com"]
    IngestRunFactory(finished_at=timezone.now(), new=0, error="")
    status = health_status()

    monkeypatch.setattr("watch.health.send_mail", lambda *args, **kwargs: 0)
    assert send_quiet_alert(status) is False
    assert cache.get(QUIET_CACHE_KEY) is None

    monkeypatch.undo()
    assert send_quiet_alert(status) is True
    assert len(mail.outbox) == 1


@pytest.mark.django_db
def test_check_command_sends_alert_email_when_stale(settings):
    settings.WATCH_ALERT_EMAIL = ["ops@example.com"]
    IngestRunFactory(finished_at=timezone.now() - timedelta(hours=8), error="")

    with pytest.raises(CommandError):
        call_command("check_watch_health")

    assert len(mail.outbox) == 1
    assert mail.outbox[0].subject == "CivicBand Watch: ingest is stale"
