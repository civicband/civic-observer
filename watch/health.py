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
STALE_ALERT_CACHE_TIMEOUT = 86400
INGEST_RUN_ADMIN_PATH = "/admin/watch/ingestrun/"
STALE_ALERT_SUBJECT = "CivicBand Watch: ingest is stale"

HealthStatus = dict[str, object]


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
    last_success = status.get("last_success")
    hours_since = status.get("hours_since_success")
    if isinstance(last_success, datetime):
        last_success_text = timezone.localtime(last_success).strftime(
            "%Y-%m-%d %H:%M %Z"
        )
    else:
        last_success_text = "never"
    if isinstance(hours_since, int | float):
        hours_text = f"{float(hours_since):.1f}"
    else:
        hours_text = "unknown"

    return "\n".join(
        [
            "CivicBand Watch has not completed a successful ingest recently.",
            "",
            f"Last successful ingest: {last_success_text}",
            f"Hours since last success: {hours_text}",
            _last_run_line(),
            "",
            f"Review ingest runs: {INGEST_RUN_ADMIN_PATH}",
            "",
        ]
    )


def _release_alert_key(cache_key: str) -> None:
    """Drop the daily guard key so a later run can retry the alert."""
    try:
        cache.delete(cache_key)
    except Exception:
        logger.exception("stale alert cache delete failed")


def send_stale_alert(status: HealthStatus) -> bool:
    """Send at most one stale-ingest alert per local day.

    Returns ``True`` only when an email was actually sent. Failed sends release
    the daily cache key so a later cron run can retry. Cache failures are
    logged and treated as "not sent" rather than crashing the caller.
    """
    if not status.get("stale"):
        return False
    recipients = list(settings.WATCH_ALERT_EMAIL)
    if not recipients:
        return False

    cache_key = f"{STALE_ALERT_CACHE_PREFIX}{timezone.localdate().isoformat()}"
    # Build the message before touching the cache so a body-construction bug
    # surfaces instead of being swallowed by the send error handler.
    subject = STALE_ALERT_SUBJECT
    body = _alert_body(status)

    try:
        acquired = cache.add(cache_key, True, timeout=STALE_ALERT_CACHE_TIMEOUT)
    except Exception:
        logger.exception("stale alert cache add failed")
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
        logger.exception("stale alert send failed")
        _release_alert_key(cache_key)
        return False

    if not sent:
        _release_alert_key(cache_key)
        return False
    return True
