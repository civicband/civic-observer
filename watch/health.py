"""Health signals for the CivicBand Watch ingest pipeline.

The administrator dashboard and the ``check_watch_health`` command both read
from here. Nothing in this module exposes the f5bot feed URL, raw snippets, or
the access hash: the alert body only carries counts and a link to the ingest
run changelist.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from django.conf import settings
from django.core.cache import cache
from django.core.mail import send_mail
from django.db.models import Sum
from django.utils import timezone

from watch.models import IngestRun

logger = logging.getLogger(__name__)

STALE_ALERT_CACHE_PREFIX = "watch:stale-alert:"
QUIET_ALERT_CACHE_PREFIX = "watch:quiet-alert:"
ALERT_CACHE_TIMEOUT = 86400
INGEST_RUN_ADMIN_PATH = "/admin/watch/ingestrun/"
STALE_ALERT_SUBJECT = "CivicBand Watch: ingest is stale"
QUIET_ALERT_SUBJECT = "CivicBand Watch: no new alerts"

HealthStatus = dict[str, object]


def format_last_success(value: object) -> str:
    """Format a success time from either a datetime or an ``IngestRun``."""
    if isinstance(value, IngestRun):
        value = value.finished_at
    if isinstance(value, datetime):
        return timezone.localtime(value).strftime("%Y-%m-%d %H:%M %Z")
    return "never"


def _format_hours(value: object) -> str:
    """Format an hours-since-success value for email bodies."""
    if isinstance(value, int | float):
        return f"{float(value):.1f}"
    return "unknown"


def _day_start(now: datetime) -> datetime:
    """Return local midnight for ``now`` as an aware datetime."""
    return timezone.localtime(now).replace(hour=0, minute=0, second=0, microsecond=0)


def _new_mentions_since(start: datetime, end: datetime | None = None) -> int:
    """Sum ``new`` for runs that started in ``[start, end]``."""
    runs = IngestRun.objects.filter(started_at__gte=start)
    if end is not None:
        runs = runs.filter(started_at__lte=end)
    total = runs.aggregate(total=Sum("new"))["total"]
    return int(total or 0)


def health_status(now: datetime | None = None) -> HealthStatus:
    """Return the current ingest health snapshot.

    ``last_success`` is the newest run that finished without an error.
    ``hours_since_success`` is measured from that run's ``finished_at``.
    ``alerts_today`` sums ``new`` for runs started on the local date, and
    ``quiet`` is true when no run started in the last 24 hours added mentions.
    """
    now = now or timezone.now()
    last_success = (
        IngestRun.objects.filter(finished_at__isnull=False, error="")
        .order_by("-finished_at")
        .first()
    )
    hours_since_success: float | None = None
    if last_success is not None and last_success.finished_at is not None:
        hours_since_success = (now - last_success.finished_at).total_seconds() / 3600

    stale_hours = settings.WATCH_STALE_HOURS
    stale = hours_since_success is None or hours_since_success > stale_hours
    quiet = _new_mentions_since(now - timedelta(hours=24)) == 0

    return {
        "last_success": last_success,
        "hours_since_success": hours_since_success,
        "alerts_today": _new_mentions_since(_day_start(now), end=now),
        "stale": stale,
        "quiet": quiet,
    }


def _last_run_line() -> str:
    """Describe the newest run's counts without exposing its source."""
    last_run = IngestRun.objects.order_by("-started_at").first()
    if last_run is None:
        return "Last run: none recorded"
    return f"Last run: pages={last_run.pages} seen={last_run.seen} new={last_run.new}"


def _alert_body(status: HealthStatus) -> str:
    return "\n".join(
        [
            "CivicBand Watch has not completed a successful ingest recently.",
            "",
            f"Last successful ingest: {format_last_success(status.get('last_success'))}",
            f"Hours since last success: {_format_hours(status.get('hours_since_success'))}",
            _last_run_line(),
            "",
            f"Review ingest runs: {INGEST_RUN_ADMIN_PATH}",
            "",
        ]
    )


def _quiet_alert_body(status: HealthStatus) -> str:
    return "\n".join(
        [
            "CivicBand Watch recorded no new alerts today.",
            "",
            f"Last successful ingest: {format_last_success(status.get('last_success'))}",
            f"Hours since last success: {_format_hours(status.get('hours_since_success'))}",
            f"New mentions today: {status.get('alerts_today')}",
            "",
            f"Review ingest runs: {INGEST_RUN_ADMIN_PATH}",
            "",
        ]
    )


def _release_alert_key(cache_key: str, *, label: str) -> None:
    """Drop the daily guard key so a later run can retry the alert."""
    try:
        cache.delete(cache_key)
    except Exception:
        logger.exception("%s cache delete failed", label)


def _send_daily_alert(
    *,
    cache_prefix: str,
    subject: str,
    body: str,
    label: str,
) -> bool:
    """Send one cached-per-day alert and return whether it was sent."""
    recipients = list(settings.WATCH_ALERT_EMAIL)
    if not recipients:
        return False

    cache_key = f"{cache_prefix}{timezone.localdate().isoformat()}"
    try:
        acquired = cache.add(cache_key, True, timeout=ALERT_CACHE_TIMEOUT)
    except Exception:
        logger.exception("%s cache add failed", label)
        return False
    if not acquired:
        return False

    try:
        sent = send_mail(
            subject,
            body,
            settings.DEFAULT_FROM_EMAIL,
            recipients,
            fail_silently=False,
        )
    except Exception:
        logger.exception("%s send failed", label)
        _release_alert_key(cache_key, label=label)
        return False

    if not sent:
        _release_alert_key(cache_key, label=label)
        return False
    return True


def send_stale_alert(status: HealthStatus) -> bool:
    """Send at most one stale-ingest alert per local day.

    Returns ``True`` only when an email was actually sent. Failed sends release
    the daily cache key so a later cron run can retry. Cache failures are
    logged and treated as "not sent" rather than crashing the caller.
    """
    if not status.get("stale"):
        return False

    # Build the message before touching the cache so a body-construction bug
    # surfaces instead of being swallowed by the send error handler.
    body = _alert_body(status)
    return _send_daily_alert(
        cache_prefix=STALE_ALERT_CACHE_PREFIX,
        subject=STALE_ALERT_SUBJECT,
        body=body,
        label="stale alert",
    )


def send_quiet_alert(status: HealthStatus) -> bool:
    """Send at most one quiet-day alert per local day.

    A stale pipeline takes precedence, so this returns ``False`` when the
    status is also stale.
    """
    if status.get("stale") or not status.get("quiet"):
        return False

    body = _quiet_alert_body(status)
    return _send_daily_alert(
        cache_prefix=QUIET_ALERT_CACHE_PREFIX,
        subject=QUIET_ALERT_SUBJECT,
        body=body,
        label="quiet alert",
    )
