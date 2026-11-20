import logging
from types import SimpleNamespace

import django_rq
import pytest

from tests.factories import ThreadFactory
from watch import enrichment, ingest
from watch.models import Mention, Thread


def item(alert_id, *, post_id="abc123", title="First title"):
    return {
        "id": alert_id,
        "url": f"https://www.reddit.com/r/TestTown/comments/{post_id}/example/",
        "title": f"civic - Reddit Posts - {title}",
        "content_html": "A <strong>civic</strong> meeting",
        "date_published": "2026-01-01T00:00:00Z",
        "username": "resident",
        "group": "group",
        "tags": ["civic", "Reddit Posts"],
    }


def break_rq(monkeypatch):
    def no_queue(name):
        raise RuntimeError("no redis")

    monkeypatch.setattr(django_rq, "get_queue", no_queue)


@pytest.mark.django_db
def test_enrich_refreshes_thread_hits(monkeypatch):
    thread = ThreadFactory()
    monkeypatch.setattr("watch.enrichment.refresh_thread_hits", lambda t: 4)
    assert enrichment.enrich_thread(str(thread.id)) == {
        "status": "ok",
        "thread_id": str(thread.id),
        "hits": 4,
    }


@pytest.mark.django_db
def test_enrich_passes_loaded_thread_to_refresh(monkeypatch):
    thread = ThreadFactory()
    seen = []

    def fake_refresh(candidate):
        seen.append(candidate)
        return 0

    monkeypatch.setattr("watch.enrichment.refresh_thread_hits", fake_refresh)

    result = enrichment.enrich_thread(str(thread.id))

    assert result["hits"] == 0
    assert seen == [thread]


@pytest.mark.django_db
def test_enrich_with_no_subject_places_reports_zero_hits():
    thread = ThreadFactory()
    assert enrichment.enrich_thread(str(thread.id)) == {
        "status": "ok",
        "thread_id": str(thread.id),
        "hits": 0,
    }


def test_tasks_module_reexports_enrich_thread():
    from watch import tasks

    assert tasks.enrich_thread is enrichment.enrich_thread


@pytest.mark.django_db
def test_enrich_returns_not_found_for_unknown_thread(monkeypatch):
    monkeypatch.setattr(
        "watch.enrichment.refresh_thread_hits",
        lambda t: pytest.fail("must not refresh a missing thread"),
    )
    assert enrichment.enrich_thread("missing") == {
        "status": "not_found",
        "thread_id": "missing",
    }


@pytest.mark.django_db
def test_enrich_returns_not_found_for_absent_uuid(monkeypatch):
    thread_id = "00000000-0000-0000-0000-000000000000"
    monkeypatch.setattr(
        "watch.enrichment.refresh_thread_hits",
        lambda t: pytest.fail("must not refresh a missing thread"),
    )
    assert enrichment.enrich_thread(thread_id) == {
        "status": "not_found",
        "thread_id": thread_id,
    }


def test_enqueue_enrichment_uses_default_queue(monkeypatch):
    calls = {}

    class FakeQueue:
        name = "default"

        def enqueue(self, func, *args, **kwargs):
            calls["call"] = (func, args, kwargs)
            return SimpleNamespace(id="job-1")

    def fake_get_queue(name):
        calls["queue"] = name
        return FakeQueue()

    monkeypatch.setattr(django_rq, "get_queue", fake_get_queue)
    assert ingest.enqueue_enrichment("thread-id") == "job-1"
    assert calls["queue"] == "default"
    assert calls["call"] == (
        enrichment.enrich_thread,
        ("thread-id",),
        {"job_timeout": 120},
    )


def test_enqueue_enrichment_tolerates_queue_returning_none(monkeypatch):
    class FakeQueue:
        def enqueue(self, func, *args, **kwargs):
            return None

    monkeypatch.setattr(django_rq, "get_queue", lambda name: FakeQueue())
    assert ingest.enqueue_enrichment("thread-id") is None


def test_enqueue_failure_does_not_break_ingest(monkeypatch, caplog):
    break_rq(monkeypatch)
    with caplog.at_level(logging.ERROR, logger="watch.ingest"):
        assert ingest.enqueue_enrichment("thread-id") is None
    assert "thread-id" in caplog.text


@pytest.mark.django_db
def test_ingest_page_still_stores_mentions_when_enqueue_fails(monkeypatch):
    break_rq(monkeypatch)
    assert ingest.ingest_page({"items": [item("alert-1")]}) == (0, 1, 0)
    assert Mention.objects.filter(pk="alert-1").exists()
    assert Thread.objects.get().mention_count == 1


@pytest.mark.django_db
def test_ingest_page_enqueues_once_per_changed_thread(monkeypatch):
    enqueued: list[str] = []
    monkeypatch.setattr(ingest, "enqueue_enrichment", enqueued.append)

    page = {
        "items": [
            item("alert-1"),
            item("alert-2"),
            item("alert-3", post_id="def456"),
        ]
    }
    assert ingest.ingest_page(page) == (0, 3, 0)
    expected = {
        str(thread_id) for thread_id in Thread.objects.values_list("id", flat=True)
    }
    assert len(enqueued) == 2
    assert set(enqueued) == expected
    assert all(isinstance(thread_id, str) for thread_id in enqueued)


@pytest.mark.django_db
def test_poll_feed_enqueues_after_each_page(monkeypatch):
    enqueued: list[str] = []
    monkeypatch.setattr(ingest, "enqueue_enrichment", enqueued.append)
    pages = {
        "one": {"items": [item("alert-1")], "next_url": "two"},
        "two": {"items": [item("alert-2", post_id="def456")]},
    }

    def fetch(url):
        if url == "two":
            # The first page is stored and enqueued before page two is fetched.
            assert len(enqueued) == 1
        return pages[url]

    assert ingest.poll_feed("one", fetch=fetch) == ingest.IngestStats(2, 0, 2)
    expected = {
        str(thread_id) for thread_id in Thread.objects.values_list("id", flat=True)
    }
    assert len(enqueued) == 2
    assert set(enqueued) == expected


@pytest.mark.django_db
def test_ingest_page_enqueues_existing_thread_when_new_mention_arrives(monkeypatch):
    ingest.ingest_page({"items": [item("alert-1")]})
    enqueued: list[str] = []
    monkeypatch.setattr(ingest, "enqueue_enrichment", enqueued.append)

    assert ingest.ingest_page({"items": [item("alert-2")]}) == (0, 1, 0)
    assert enqueued == [str(Thread.objects.get().id)]


@pytest.mark.django_db
def test_ingest_page_does_not_enqueue_when_nothing_changed(monkeypatch):
    page = {"items": [item("alert-1"), item("alert-2")]}
    ingest.ingest_page(page)
    enqueued: list[str] = []
    monkeypatch.setattr(ingest, "enqueue_enrichment", enqueued.append)

    assert ingest.ingest_page(page) == (2, 0, 0)
    assert enqueued == []
