"""Parse f5bot JSON Feed items into normalized mention data."""

from __future__ import annotations

import html
import json
import re
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

REDDIT_RE = re.compile(
    r"^/r/(?P<sub>[^/]+)/comments/(?P<post>[a-z0-9]+)"
    r"(?:/c/(?P<comment>[a-z0-9]+)"  # f5bot's short comment form
    r"|/[^/]*/(?P<comment2>[a-z0-9]+)"  # canonical /slug/<comment_id>
    r"|/[^/]*)?/?$",
    re.I,
)
STRONG_RE = re.compile(r"<strong>(.*?)</strong>", re.I | re.S)
TAG_RE = re.compile(r"<[^>]+>")
ALERT_NUM_RE = re.compile(r"-(\d+)$")


def to_utc_iso(value: str | None) -> str | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)  # noqa: UP017 - preserve the port
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")  # noqa: UP017


def html_to_text(fragment: str) -> str:
    return " ".join(html.unescape(TAG_RE.sub("", fragment or "")).split())


def strip_title_prefix(title: str, keyword: str | None, source_tag: str | None) -> str:
    """f5bot titles look like '<keyword> - <source tag> - <post title>'."""
    title = html.unescape(title or "")
    if keyword and source_tag:
        prefix = f"{keyword} - {source_tag} - "
        if title.lower().startswith(prefix.lower()):
            return title[len(prefix) :]
    parts = title.split(" - ", 2)
    return parts[2] if len(parts) == 3 else title


def classify_site(host: str) -> str:
    host = host.lower()
    if host.endswith("reddit.com") or host == "redd.it":
        return "reddit"
    if host.endswith("ycombinator.com"):
        return "hackernews"
    if host.endswith("lobste.rs"):
        return "lobsters"
    return "other"


def parse_item(item: dict) -> dict:
    tags = item.get("tags") or []
    keyword = tags[0] if tags else None
    source_tag = tags[1] if len(tags) > 1 else None
    url = item.get("url") or ""
    parsed = urlparse(url)
    site = classify_site(parsed.netloc)

    subreddit = post_id = comment_id = None
    kind = None
    if site == "reddit":
        m = REDDIT_RE.match(parsed.path)
        if m:
            subreddit = m.group("sub").lower()
            post_id = m.group("post").lower()
            comment_id = m.group("comment") or m.group("comment2") or None
            comment_id = comment_id.lower() if comment_id else None
    if source_tag:
        kind = "comment" if "comment" in source_tag.lower() else "post"
    elif site == "reddit":
        kind = "comment" if comment_id else "post"

    if site == "reddit" and subreddit and post_id:
        thread_key = f"reddit:{subreddit}/{post_id}"
    elif site == "hackernews":
        # HN comment urls don't carry the story id; fall back to the item itself
        thread_key = "hn:" + (parse_qs(parsed.query).get("id", [url])[0])
    else:
        thread_key = url

    content = item.get("content_html") or ""
    alert_num = ALERT_NUM_RE.search(item.get("id", ""))
    return {
        "id": item["id"],
        "alert_num": int(alert_num.group(1)) if alert_num else None,
        "url": url,
        "site": site,
        "kind": kind,
        "source_tag": source_tag,
        "keyword": keyword.lower() if keyword else None,
        "subreddit": subreddit,
        "post_id": post_id,
        "comment_id": comment_id,
        "thread_key": thread_key,
        "post_title": strip_title_prefix(item.get("title", ""), keyword, source_tag),
        "snippet_text": html_to_text(content),
        "snippet_html": content,
        "highlights": json.dumps(
            sorted({html_to_text(h).lower() for h in STRONG_RE.findall(content)})
        ),
        "author": item.get("username") or None,
        "grp": item.get("group") or None,
        "published_at": to_utc_iso(item.get("date_published")),
        "raw": json.dumps(item, ensure_ascii=False),
    }
