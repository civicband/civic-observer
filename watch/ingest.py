"""Ingest f5bot feed pages into watch threads and mentions."""

import json
import re
from dataclasses import dataclass
from datetime import datetime

import httpx
from django.db import transaction
from django.utils import timezone

from watch.models import IngestRun, Mention, Subreddit, Thread
from watch.parsing import parse_item


@dataclass(frozen=True)
class IngestStats:
    pages: int
    seen: int
    new: int
    skipped: int = 0


def fetch_json(url: str, *, client: httpx.Client | None = None) -> dict:
    if client is None:
        with httpx.Client() as owned_client:
            return fetch_json(url, client=owned_client)
    response = client.get(url)
    response.raise_for_status()
    return response.json()


@transaction.atomic
def ingest_page(page: dict) -> tuple[int, int, int]:
    seen = new = skipped = 0
    for item in page.get("items") or []:
        row = parse_item(item)
        if row["site"] != "reddit":
            skipped += 1
            continue
        if Mention.objects.filter(pk=row["id"]).exists():
            seen += 1
            continue

        published_at = (
            datetime.fromisoformat(row["published_at"]) if row["published_at"] else None
        )
        observed_at = published_at or timezone.now()
        subreddit = None
        if row["subreddit"]:
            subreddit, _ = Subreddit.objects.get_or_create(
                name=row["subreddit"].lower(),
                defaults={
                    "kind": Subreddit.Kind.GENERAL,
                    "source": "",
                    "confidence": "",
                },
            )
        thread, _ = Thread.objects.get_or_create(
            key=row["thread_key"],
            defaults={
                "subreddit": subreddit,
                "title": row["post_title"],
                "first_seen": observed_at,
                "last_seen": observed_at,
            },
        )
        # Serialize updates to an existing thread, including the duplicate check.
        thread = Thread.objects.select_for_update().get(pk=thread.pk)
        if Mention.objects.filter(pk=row["id"]).exists():
            seen += 1
            continue

        Mention.objects.create(
            id=row["id"],
            thread=thread,
            alert_num=row["alert_num"],
            url=row["url"],
            site=row["site"],
            kind=row["kind"] or Mention.Kind.OTHER,
            source_tag=row["source_tag"] or "",
            keyword=row["keyword"] or "",
            subreddit_name=row["subreddit"] or "",
            post_id=row["post_id"] or "",
            comment_id=row["comment_id"] or "",
            post_title=row["post_title"],
            snippet_text=row["snippet_text"],
            snippet_html=row["snippet_html"],
            highlights=json.loads(row["highlights"]),
            author=row["author"] or "",
            grp=row["grp"] or "",
            published_at=published_at,
            raw_json=item,
        )
        if thread.subreddit_id is None and subreddit is not None:
            thread.subreddit = subreddit
        if not thread.title:
            thread.title = row["post_title"]
        thread.first_seen = min(thread.first_seen, observed_at)
        thread.last_seen = max(thread.last_seen, observed_at)
        thread.mention_count += 1
        thread.save()
        new += 1
    return seen, new, skipped


def poll_feed(feed_url: str, *, fetch=fetch_json, max_pages: int = 20) -> IngestStats:
    pages = seen = new = skipped = 0
    url = feed_url
    while url and pages < max_pages:
        page = fetch(url)
        pages += 1
        page_seen, page_new, page_skipped = ingest_page(page)
        seen += page_seen
        new += page_new
        skipped += page_skipped
        if page_seen or not page.get("items"):
            break
        url = page.get("next_url")
    return IngestStats(pages=pages, seen=seen, new=new, skipped=skipped)


def run_ingest(
    feed_url: str, *, fetch=fetch_json, max_pages: int = 20, source: str = "feed"
) -> IngestRun:
    # Source is a label, not an arbitrary string that might contain credentials.
    run = IngestRun.objects.create(
        source=source if source in {"feed", "file"} else "feed"
    )
    try:
        stats = poll_feed(feed_url, fetch=fetch, max_pages=max_pages)
        run.pages = stats.pages
        run.seen = stats.seen
        run.new = stats.new
        run.skipped = stats.skipped
    except Exception as exc:
        message = (
            str(exc).replace(feed_url, "[feed url redacted]") if feed_url else str(exc)
        )
        # Pagination URLs can contain the same secrets with different parameters.
        message = re.sub(r"https?://[^\s'\"<>]+", "[feed url redacted]", message)
        run.error = f"{type(exc).__name__}: {message}"
    finally:
        run.finished_at = timezone.now()
        run.save()
    return run
