"""Template filters and tags for the staff-only watch pages."""

from __future__ import annotations

import re
from collections.abc import Iterable
from urllib.parse import urlsplit

from django import template
from django.utils.html import escape
from django.utils.safestring import SafeString, mark_safe

register = template.Library()


@register.filter
def external_url(value: object) -> str:
    """Return ``value`` when it is an absolute http(s) URL, else ``""``."""
    if not value:
        return ""
    url = str(value).strip()
    try:
        parsed = urlsplit(url)
    except ValueError:
        return ""
    if parsed.scheme.lower() in {"http", "https"} and parsed.netloc:
        return url
    return ""


@register.filter
def thread_url(value: object) -> str:
    """Return the public thread URL for a ``Thread.key``.

    Reddit keys are stored as ``reddit:<subreddit>/<post id>``; everything
    else (Hacker News, arbitrary URLs) falls back to ``value`` itself when it
    is an absolute http(s) URL. Anything unrecognised returns ``""`` so the
    template can render plain text instead of a broken link.
    """
    key = str(value or "").strip()
    if key.startswith("reddit:"):
        subreddit, _, post_id = key[len("reddit:") :].partition("/")
        if not re.fullmatch(r"[A-Za-z0-9_]+", subreddit or ""):
            return ""
        if not re.fullmatch(r"[A-Za-z0-9]+", post_id):
            return ""
        return f"https://www.reddit.com/r/{subreddit}/comments/{post_id}/"
    return external_url(key)


def _keywords(keywords: str | Iterable[str] | None) -> list[str]:
    """Normalize a keyword string or iterable into a list of non-empty terms."""
    if not keywords:
        return []
    values = [keywords] if isinstance(keywords, str) else list(keywords)
    cleaned: list[str] = []
    for value in values:
        term = str(value).strip()
        if term:
            cleaned.append(term)
    return cleaned


@register.simple_tag
def highlight_keywords(
    text: str | None, keywords: str | Iterable[str] | None
) -> SafeString:
    """Escape ``text`` and wrap case-insensitive keyword matches in ``<mark>``.

    Matching happens against the raw text so keyword terms are never matched
    inside an HTML entity. Each raw segment is escaped before it is emitted.
    """
    raw = text or ""
    terms = sorted(set(_keywords(keywords)), key=len, reverse=True)
    if not terms:
        return mark_safe(escape(raw))

    pattern = re.compile("|".join(re.escape(term) for term in terms), re.IGNORECASE)
    parts: list[str] = []
    cursor = 0
    for match in pattern.finditer(raw):
        parts.append(escape(raw[cursor : match.start()]))
        parts.append(f"<mark>{escape(match.group(0))}</mark>")
        cursor = match.end()
    parts.append(escape(raw[cursor:]))
    return mark_safe("".join(parts))


@register.simple_tag
def citation(hit) -> str:
    """Return a hit as ``[Meeting name (YYYY-MM-DD)](url)`` plain text."""
    name = getattr(hit, "meeting_name", "") or "Meeting"
    meeting_date = getattr(hit, "meeting_date", None)
    date_text = meeting_date.isoformat() if meeting_date else "—"
    url = getattr(hit, "civic_band_url", "") or ""
    return f"[{name} ({date_text})]({url})"
