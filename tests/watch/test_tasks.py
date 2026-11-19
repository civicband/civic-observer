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
def test_enrich_stub_returns_thread_id():
    thread = ThreadFactory()
    assert enrichment.enrich_thread(str(thread.id)) == {
        "status": "stubbed",
        "thread_id": str(thread.id),
    }


def test_tasks_module_exposes_rq_entrypoint():
    from watch import tasks

    assert tasks.enrich_thread is enrichment.enrich_thread


@pytest.mark.django_db
def test_enrich_stub_returns_unknown_thread_id_unchanged():
    assert enrichment.enrich_thread("missing") == {
        "status": "stubbed",
        "thread_id": "missing",
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
    expected = [
        str(thread_id)
        for thread_id in Thread.objects.order_by("key").values_list("id", flat=True)
    ]
    assert enqueued == expected
    assert all(isinstance(thread_id, str) for thread_id in enqueued)


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
