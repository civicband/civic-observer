"""Admin index tag for the ingest health banner."""

from __future__ import annotations

from datetime import datetime

from django import template
from django.template import Context
from django.utils import timezone
from django.utils.html import format_html
from django.utils.safestring import SafeString

from watch.health import health_status

register = template.Library()


@register.simple_tag(takes_context=True)
def watch_health(context: Context) -> SafeString:
    """Render the last ingest status for staff users, nothing for everyone else."""
    request = context.get("request")
    user = getattr(request, "user", None)
    if user is None or not user.is_staff:
        return format_html("")

    status = health_status()
    last_success = status.get("last_success")
    hours_since = status.get("hours_since_success")
    stale = bool(status.get("stale"))
    quiet = bool(status.get("quiet"))

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

    stale_class = "watch-health-stale" if stale else "watch-health-ok"
    quiet_class = "watch-health-quiet" if quiet else "watch-health-ok"

    return format_html(
        '<div class="module watch-health">'
        "<h2>Ingest health</h2>"
        "<ul>"
        "<li>Last successful ingest: <strong>{}</strong></li>"
        "<li>Hours since last success: {}</li>"
        "<li>New mentions today: {}</li>"
        '<li>Status: <span class="{}">stale={}</span> '
        '<span class="{}">quiet={}</span></li>'
        "</ul>"
        "</div>",
        last_success_text,
        hours_text,
        status.get("alerts_today"),
        stale_class,
        "yes" if stale else "no",
        quiet_class,
        "yes" if quiet else "no",
    )
