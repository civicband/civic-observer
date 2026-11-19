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
from redis.exceptions import LockError

from watch.enrichment import enrich_thread
from watch.models import IngestRun, Mention, Subreddit, Thread
from watch.parsing import parse_item

logger = logging.getLogger(__name__)


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
            try:
                lock.release()
            except LockError as exc:
                # A long backfill can outlive the lock; that must not turn an
                # otherwise committed run into a traceback.
                logger.warning("Could not release ingest lock: %s", type(exc).__name__)


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
    seen, new, skipped, changed_threads = _ingest_page(page, created_ids=set())
    _enqueue_changed_threads(changed_threads)
    return seen, new, skipped


def enqueue_enrichment(thread_id: str) -> str | None:
    """Queue enrichment for a thread without letting queue errors break ingest."""
    try:
        job = django_rq.get_queue("default").enqueue(
            enrich_thread, str(thread_id), job_timeout=120
        )
    except Exception:
        # A missing or unreachable queue must never fail or roll back the ingest.
        logger.exception("Could not enqueue enrichment for thread %s", thread_id)
        return None
    return getattr(job, "id", None)


def _enqueue_changed_threads(thread_ids: list[str]) -> None:
    # A page can touch the same thread many times; enrich each one once.
    for thread_id in dict.fromkeys(thread_ids):
        enqueue_enrichment(thread_id)


def _safe_item_id(item: dict) -> str:
    raw = item.get("id") if isinstance(item, dict) else None
    if isinstance(raw, str) and raw.strip():
        return raw
    return "<missing id>"


@transaction.atomic
def _ingest_page(
    page: dict, *, created_ids: set[str]
) -> tuple[int, int, int, list[str]]:
    seen = new = skipped = 0
    page_ids: set[str] = set()
    new_ids: set[str] = set()
    changed_threads: list[str] = []
    for item in page.get("items") or []:
        try:
            if not all(
                isinstance(item.get(field), str) and item[field].strip()
                for field in ("id", "url")
            ):
                logger.warning(
                    "Skipping feed item %s: missing or blank id/url",
                    _safe_item_id(item),
                )
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
        except Exception as exc:
            # Malformed input should not discard the rest of the page.
            logger.warning(
                "Skipping feed item %s during parse: %s: %s",
                _safe_item_id(item),
                type(exc).__name__,
                exc,
            )
            skipped += 1
            continue
        if row["site"] != "reddit":
            logger.debug("Skipping non-Reddit item %s", row["id"])
            skipped += 1
            continue
        if row["id"] in page_ids or row["id"] in created_ids:
            logger.debug("Skipping duplicate item %s", row["id"])
            skipped += 1
            continue
        try:
            stored_thread_id = _store_item(item, row, published_at)
        except (DataError, ValueError) as exc:
            logger.warning(
                "Skipping feed item %s during store: %s: %s",
                row["id"],
                type(exc).__name__,
                exc,
            )
            skipped += 1
            continue
        page_ids.add(row["id"])
        if stored_thread_id is None:
            seen += 1
            continue
        changed_threads.append(stored_thread_id)
        new_ids.add(row["id"])
        new += 1
    created_ids.update(new_ids)
    return seen, new, skipped, changed_threads


@transaction.atomic
def _store_item(item: dict, row: dict, published_at: datetime | None) -> str | None:
    if Mention.objects.filter(pk=row["id"]).exists():
        return None
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
        return None

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
    return str(thread.pk)


def _poll_feed_pages(feed_url: str, *, fetch, max_pages: int):
    pages = seen = new = skipped = 0
    created_ids: set[str] = set()
    url = feed_url
    while url and pages < max_pages:
        page = fetch(url)
        page_seen, page_new, page_skipped, changed_threads = _ingest_page(
            page, created_ids=created_ids
        )
        # Enqueue after the page transaction commits so a rollback cannot leave
        # jobs pointing at threads that were never stored.
        _enqueue_changed_threads(changed_threads)
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
