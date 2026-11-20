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
    """Escape ``text`` and wrap case-insensitive keyword matches in ``<mark>``."""
    escaped = escape(text or "")
    terms = sorted(
        {escape(term) for term in _keywords(keywords)}, key=len, reverse=True
    )
    if not terms:
        return mark_safe(escaped)

    pattern = re.compile("|".join(re.escape(term) for term in terms), re.IGNORECASE)
    highlighted = pattern.sub(lambda match: f"<mark>{match.group(0)}</mark>", escaped)
    return mark_safe(highlighted)


@register.simple_tag
def citation(hit) -> str:
    """Return a hit as ``[Meeting name (YYYY-MM-DD)](url)`` plain text."""
    name = getattr(hit, "meeting_name", "") or "Meeting"
    meeting_date = getattr(hit, "meeting_date", None)
    date_text = meeting_date.isoformat() if meeting_date else "—"
    url = getattr(hit, "civic_band_url", "") or ""
    return f"[{name} ({date_text})]({url})"
