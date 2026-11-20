"""Template filters and tags for the staff-only watch pages."""

from __future__ import annotations

import re
from collections.abc import Iterable

from django import template
from django.utils.html import escape
from django.utils.safestring import SafeString, mark_safe

register = template.Library()


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
