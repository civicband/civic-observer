"""Ingest f5bot feed pages into watch threads and mentions."""

import json
import logging
import re
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime

import django_rq
import httpx
from django.conf import settings
from django.db import DataError, transaction
from django.utils import timezone

from watch.models import IngestRun, Mention, Subreddit, Thread
from watch.parsing import parse_item


@dataclass(frozen=True)
class IngestStats:
    pages: int
    seen: int
    new: int
    skipped: int = 0


@contextmanager
def acquire_ingest_lock():
    lock = django_rq.get_connection("default").lock(
        "watch:ingest", timeout=settings.WATCH_INGEST_LOCK_SECONDS, blocking=False
    )
    acquired = lock.acquire()
    try:
        yield acquired
    finally:
        if acquired:
            lock.release()


@contextmanager
def _hide_http_logs():
    # HTTPX logs full request URLs at INFO; httpcore traces may log them at DEBUG.
    # Filter only this thread while fetching so unrelated HTTP logs are unaffected.
    thread_id = threading.get_ident()
    loggers = [
        logging.getLogger(name)
        for name in (
            "httpx",
            "httpcore.connection",
            "httpcore.http11",
            "httpcore.http2",
            "httpcore.proxy",
            "httpcore.socks",
        )
    ]

    def not_this_thread(record):
        return record.thread != thread_id

    for logger in loggers:
        logger.addFilter(not_this_thread)
    try:
        yield
    finally:
        for logger in loggers:
            logger.removeFilter(not_this_thread)


def fetch_json(url: str, *, client: httpx.Client | None = None) -> dict:
    if client is None:
        with httpx.Client() as owned_client:
            return fetch_json(url, client=owned_client)
    with _hide_http_logs():
        response = client.get(url)
    response.raise_for_status()
    return response.json()


def ingest_page(page: dict) -> tuple[int, int, int]:
    return _ingest_page(page, created_ids=set())


@transaction.atomic
def _ingest_page(page: dict, *, created_ids: set[str]) -> tuple[int, int, int]:
    seen = new = skipped = 0
    page_ids: set[str] = set()
    new_ids: set[str] = set()
    for item in page.get("items") or []:
        try:
            if not all(
                isinstance(item.get(field), str) and item[field].strip()
                for field in ("id", "url")
            ):
                skipped += 1
                continue
            date = item.get("date_published")
            parse_input = (
                {**item, "date_published": None}
                if date is not None and not isinstance(date, str)
                else item
            )
            row = parse_item(parse_input)
            try:
                published_at = (
                    datetime.fromisoformat(row["published_at"])
                    if row["published_at"]
                    else None
                )
            except (TypeError, ValueError):
                published_at = None
        except Exception:
            # Malformed input should not discard the rest of the page.
            skipped += 1
            continue
        if row["site"] != "reddit":
            skipped += 1
            continue
        if row["id"] in page_ids or row["id"] in created_ids:
            skipped += 1
            continue
        try:
            created = _store_item(item, row, published_at)
        except (DataError, ValueError):
            skipped += 1
            continue
        page_ids.add(row["id"])
        if created:
            new_ids.add(row["id"])
            new += 1
        else:
            seen += 1
    created_ids.update(new_ids)
    return seen, new, skipped


@transaction.atomic
def _store_item(item: dict, row: dict, published_at: datetime | None) -> bool:
    if Mention.objects.filter(pk=row["id"]).exists():
        return False
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
        return False

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
    thread.save(
        update_fields=[
            "subreddit",
            "title",
            "first_seen",
            "last_seen",
            "mention_count",
            "modified",
        ]
    )
    return True


def _poll_feed_pages(feed_url: str, *, fetch, max_pages: int):
    pages = seen = new = skipped = 0
    created_ids: set[str] = set()
    url = feed_url
    while url and pages < max_pages:
        page = fetch(url)
        page_seen, page_new, page_skipped = _ingest_page(page, created_ids=created_ids)
        pages += 1
        seen += page_seen
        new += page_new
        skipped += page_skipped
        yield IngestStats(pages=pages, seen=seen, new=new, skipped=skipped)
        if page_seen or not page.get("items"):
            break
        url = page.get("next_url")


def poll_feed(feed_url: str, *, fetch=fetch_json, max_pages: int = 20) -> IngestStats:
    stats = IngestStats(pages=0, seen=0, new=0)
    for page_stats in _poll_feed_pages(feed_url, fetch=fetch, max_pages=max_pages):
        stats = page_stats
    return stats


def run_ingest(
    feed_url: str, *, fetch=fetch_json, max_pages: int = 20, source: str = "feed"
) -> IngestRun:
    # Source is a label, not an arbitrary string that might contain credentials.
    run = IngestRun.objects.create(
        source=source if source in {"feed", "file"} else "feed"
    )
    stats = IngestStats(pages=0, seen=0, new=0)
    try:
        for page_stats in _poll_feed_pages(feed_url, fetch=fetch, max_pages=max_pages):
            stats = page_stats
    except Exception as exc:
        # Pagination URLs can contain the same secrets with different parameters.
        message = re.sub(r"https?://[^\s'\"<>]+", "[feed url redacted]", str(exc))
        if feed_url:
            message = message.replace(feed_url, "[feed url redacted]")
        run.error = f"{type(exc).__name__}: {message}"
    finally:
        run.pages = stats.pages
        run.seen = stats.seen
        run.new = stats.new
        run.skipped = stats.skipped
        run.finished_at = timezone.now()
        run.save()
    return run
