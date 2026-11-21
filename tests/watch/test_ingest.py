import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from django.db import OperationalError
from django.db.models.signals import pre_save

from watch.ingest import IngestStats, fetch_json, ingest_page, poll_feed, run_ingest
from watch.models import IngestRun, Mention, Subreddit, Thread

FIXTURES = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIXTURES / name).read_text())


def item(alert_id, *, date="2026-01-01T00:00:00Z", title="First title"):
    return {
        "id": alert_id,
        "url": "https://www.reddit.com/r/TestTown/comments/abc123/example/",
        "title": f"civic - Reddit Posts - {title}",
        # Distinct alerts need distinct text or ingest collapses them as
        # duplicate mentions within the shared test thread.
        "content_html": f"A <strong>civic</strong> meeting {alert_id}",
        "date_published": date,
        "username": "resident",
        "group": "group",
        "tags": ["civic", "Reddit Posts"],
    }


def fail_mention_create(monkeypatch, alert_id):
    create = Mention.objects.create

    def fail_for_id(**kwargs):
        if kwargs["id"] == alert_id:
            raise OperationalError("database unavailable")
        return create(**kwargs)

    monkeypatch.setattr(Mention.objects, "create", fail_for_id)


@pytest.mark.django_db
def test_fixture_creates_88_mentions_and_69_threads():
    assert ingest_page(load("f5bot_page_1.json")) == (0, 50, 0)
    assert ingest_page(load("f5bot_page_2.json")) == (0, 38, 0)
    assert Mention.objects.count() == 88
    assert Thread.objects.count() == 69
    la = Thread.objects.get(key="reddit:losangeles/1xzzze7")
    assert la.mention_count == 16
    assert la.subreddit is not None
    assert la.subreddit.name == "losangeles"
    assert (la.subreddit.kind, la.subreddit.source, la.subreddit.confidence) == (
        "general",
        "gazetteer",
        "low",
    )


@pytest.mark.django_db
def test_second_ingest_is_idempotent():
    ingest_page(load("f5bot_page_1.json"))
    ingest_page(load("f5bot_page_2.json"))
    assert ingest_page(load("f5bot_page_1.json")) == (50, 0, 0)
    assert Mention.objects.count() == 88
    assert Thread.objects.count() == 69
    assert Thread.objects.get(key="reddit:losangeles/1xzzze7").mention_count == 16


@pytest.mark.django_db
def test_duplicate_in_same_page_does_not_inflate_thread():
    post = item("alert-1")
    assert ingest_page({"items": [post, post]}) == (0, 1, 1)
    assert Mention.objects.count() == 1
    assert Thread.objects.get().mention_count == 1
    assert ingest_page({"items": [post, post]}) == (1, 0, 1)


@pytest.mark.django_db
def test_poll_continues_past_duplicates_created_in_current_ingest():
    post = item("alert-1")
    pages = {
        "one": {"items": [post, post], "next_url": "two"},
        "two": {"items": [post, item("alert-2")], "next_url": "three"},
        "three": {"items": [item("alert-3")]},
    }
    assert poll_feed("one", fetch=pages.__getitem__) == IngestStats(3, 0, 3, 2)
    assert Mention.objects.count() == 3
    assert Thread.objects.get().mention_count == 3


@pytest.mark.django_db
def test_fields_timestamps_and_existing_subreddit_are_preserved():
    sub = Subreddit.objects.create(name="testtown", kind="place", source="manual")
    first = item("alert-1", date="2026-02-02T12:00:00-06:00")
    earlier = item("alert-2", date="2026-01-01T00:00:00Z", title="Later title")
    assert ingest_page({"items": [first, earlier]}) == (0, 2, 0)
    thread = Thread.objects.get()
    assert thread.subreddit == sub
    sub.refresh_from_db()
    assert (sub.kind, sub.source) == ("place", "manual")
    assert thread.title == "First title"
    assert thread.first_seen == datetime(2026, 1, 1, tzinfo=UTC)
    assert thread.last_seen == datetime(2026, 2, 2, 18, tzinfo=UTC)
    mention = Mention.objects.get(pk="alert-1")
    assert mention.published_at == datetime(2026, 2, 2, 18, tzinfo=UTC)
    assert mention.highlights == ["civic"]
    assert mention.raw_json == first
    assert (mention.subreddit_name, mention.post_id, mention.author, mention.grp) == (
        "testtown",
        "abc123",
        "resident",
        "group",
    )


@pytest.mark.django_db
def test_attaches_subreddit_to_existing_thread_without_one():
    when = datetime(2026, 1, 1, tzinfo=UTC)
    thread = Thread.objects.create(
        key="reddit:testtown/abc123", first_seen=when, last_seen=when
    )
    assert ingest_page({"items": [item("alert-1")]}) == (0, 1, 0)
    thread.refresh_from_db()
    assert thread.subreddit is not None
    assert thread.subreddit.name == "testtown"
    assert thread.subreddit.kind == "general"
    assert thread.mention_count == 1


@pytest.mark.django_db
def test_rollup_does_not_overwrite_a_triage_update():
    when = datetime(2026, 1, 1, tzinfo=UTC)
    snoozed_until = datetime(2026, 3, 1, tzinfo=UTC)
    thread = Thread.objects.create(
        key="reddit:testtown/abc123", first_seen=when, last_seen=when
    )

    def edit_triage(sender, instance, **kwargs):
        Thread.objects.filter(pk=instance.pk).update(
            status=Thread.Status.SNOOZED,
            notes="Edited during ingest",
            snoozed_until=snoozed_until,
        )

    pre_save.connect(edit_triage, sender=Thread, weak=False)
    try:
        assert ingest_page({"items": [item("alert-1")]}) == (0, 1, 0)
    finally:
        pre_save.disconnect(edit_triage, sender=Thread)
    thread.refresh_from_db()
    assert thread.status == Thread.Status.SNOOZED
    assert thread.notes == "Edited during ingest"
    assert thread.snoozed_until == snoozed_until
    assert thread.mention_count == 1


@pytest.mark.django_db
def test_reddit_item_without_subreddit_or_publication_date():
    post = item("alert-1", date=None)
    post["url"] = "https://redd.it/abc123"
    post["tags"] = []
    post.pop("username")
    post.pop("group")
    assert ingest_page({"items": [post]}) == (0, 1, 0)
    mention = Mention.objects.get()
    assert mention.published_at is None
    assert mention.subreddit_name == ""
    assert mention.thread.subreddit is None


@pytest.mark.django_db
def test_malformed_items_are_skipped_and_invalid_dates_are_normalized():
    missing_id = item("alert-missing")
    del missing_id["id"]
    blank_id = item("")
    missing_url = item("alert-no-url")
    del missing_url["url"]
    broken_tags = item("alert-broken-tags")
    broken_tags["tags"] = [123]
    blank_content = item("alert-3")
    blank_content["content_html"] = ""
    non_string_date = item("alert-4", date=123)

    page = {
        "items": [
            item("alert-1"),
            missing_id,
            blank_id,
            missing_url,
            broken_tags,
            item("alert-2", date="invalid"),
            blank_content,
            non_string_date,
        ]
    }
    assert ingest_page(page) == (0, 4, 4)
    assert set(Mention.objects.values_list("id", flat=True)) == {
        "alert-1",
        "alert-2",
        "alert-3",
        "alert-4",
    }
    assert Mention.objects.get(pk="alert-2").published_at is None
    assert Mention.objects.get(pk="alert-4").published_at is None
    assert Mention.objects.get(pk="alert-3").snippet_text == ""
    assert Thread.objects.get().mention_count == 4


@pytest.mark.django_db
def test_database_failure_rolls_back_whole_page(monkeypatch):
    fail_mention_create(monkeypatch, "alert-2")
    with pytest.raises(OperationalError):
        ingest_page({"items": [item("alert-1"), item("alert-2")]})
    assert Mention.objects.count() == 0
    assert Thread.objects.count() == 0
    assert Subreddit.objects.count() == 0


@pytest.mark.django_db
def test_oversized_item_is_skipped_without_leaving_partial_rows():
    bad = item("x" * 256)
    bad["url"] = "https://www.reddit.com/r/OtherTown/comments/abc123/example/"
    assert ingest_page({"items": [item("alert-1"), bad, item("alert-2")]}) == (
        0,
        2,
        1,
    )
    assert set(Mention.objects.values_list("id", flat=True)) == {"alert-1", "alert-2"}
    assert Thread.objects.get().mention_count == 2
    assert not Subreddit.objects.filter(name="othertown").exists()


@pytest.mark.django_db
def test_poll_feed_follows_next_url_then_stops():
    pages = {
        "fixture:page-1": load("f5bot_page_1.json"),
        "fixture:page-2": load("f5bot_page_2.json"),
    }
    assert poll_feed("fixture:page-1", fetch=pages.__getitem__) == IngestStats(
        pages=2, seen=0, new=88
    )
    assert poll_feed("fixture:page-1", fetch=pages.__getitem__) == IngestStats(
        pages=1, seen=50, new=0
    )


@pytest.mark.django_db
def test_non_reddit_items_are_skipped():
    page = load("f5bot_page_1.json")
    page["items"][0]["url"] = "https://news.ycombinator.com/item?id=4242"
    assert ingest_page(page) == (0, 49, 1)
    assert Mention.objects.count() == 49


@pytest.mark.django_db
def test_poll_feed_stops_on_empty_page_and_max_pages():
    pages = {
        "one": {"items": [item("alert-1")], "next_url": "two"},
        "two": {"items": [], "next_url": "missing"},
    }
    assert poll_feed("one", fetch=pages.__getitem__, max_pages=1) == IngestStats(
        pages=1, seen=0, new=1
    )
    assert poll_feed("two", fetch=pages.__getitem__) == IngestStats(
        pages=1, seen=0, new=0
    )
    assert poll_feed("one", fetch=pages.__getitem__, max_pages=0) == IngestStats(
        pages=0, seen=0, new=0
    )


@pytest.mark.django_db
def test_poll_feed_accumulates_skipped_items():
    post = item("alert-1")
    post["url"] = "https://news.ycombinator.com/item?id=4242"
    pages = {"one": {"items": [post], "next_url": "two"}, "two": {"items": []}}
    assert poll_feed("one", fetch=pages.__getitem__) == IngestStats(2, 0, 0, 1)


@pytest.mark.django_db
def test_run_ingest_records_counts_and_hides_feed_url(settings):
    settings.F5BOT_FEED_URL = "https://f5bot.com/alert-json-feed?a=SECRET&h=SECRET"
    pages = {
        "fixture:page-1": load("f5bot_page_1.json"),
        "fixture:page-2": load("f5bot_page_2.json"),
    }
    run = run_ingest("fixture:page-1", fetch=pages.__getitem__)
    assert (run.pages, run.seen, run.new, run.skipped) == (2, 0, 88, 0)
    assert run.finished_at is not None
    assert "SECRET" not in run.source
    assert "SECRET" not in run.error


@pytest.mark.django_db
def test_run_ingest_records_error_redacting_url_and_source():
    url = "https://f5bot.com/feed?a=SECRET&h=SECRET"

    def fail(requested_url):
        assert IngestRun.objects.filter(finished_at__isnull=True).exists()
        raise ValueError(f"could not fetch {requested_url}")

    run = run_ingest(url, fetch=fail, source=url)
    run.refresh_from_db()
    assert run.finished_at is not None
    assert run.error == "ValueError: could not fetch [feed url redacted]"
    assert url not in run.source
    assert "SECRET" not in run.source


@pytest.mark.django_db
def test_run_ingest_preserves_completed_page_counts_when_fetch_fails():
    feed_url = "https://f5bot.com/feed?a=SECRET&h=SECRET"
    next_url = feed_url + "&page=2&token=OTHER_SECRET"
    missing_url = item("alert-no-url")
    del missing_url["url"]

    def fetch(url):
        if url == feed_url:
            return {"items": [item("alert-1"), missing_url], "next_url": next_url}
        assert url == next_url
        raise ValueError(f"could not fetch {url}")

    run = run_ingest(feed_url, fetch=fetch)
    run.refresh_from_db()
    assert (run.pages, run.seen, run.new, run.skipped) == (1, 0, 1, 1)
    assert run.finished_at is not None
    assert run.error.startswith("ValueError: could not fetch")
    assert "SECRET" not in run.error
    assert "OTHER_SECRET" not in run.error
    assert Mention.objects.filter(pk="alert-1").exists()


@pytest.mark.django_db
def test_run_ingest_does_not_count_rolled_back_page(monkeypatch):
    fail_mention_create(monkeypatch, "alert-3")
    pages = {
        "one": {"items": [item("alert-1")], "next_url": "two"},
        "two": {"items": [item("alert-2"), item("alert-3")]},
    }
    run = run_ingest("one", fetch=pages.__getitem__)
    run.refresh_from_db()
    assert (run.pages, run.seen, run.new, run.skipped) == (1, 0, 1, 0)
    assert run.error.startswith("OperationalError:")
    assert set(Mention.objects.values_list("id", flat=True)) == {"alert-1"}
    assert Thread.objects.get().mention_count == 1


def test_fetch_json_uses_client_and_checks_status():
    def respond(request):
        if request.url.path == "/ok":
            return httpx.Response(200, json={"items": []})
        return httpx.Response(500)

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        assert fetch_json("https://example.com/ok", client=client) == {"items": []}
        with pytest.raises(httpx.HTTPStatusError):
            fetch_json("https://example.com/error", client=client)
        assert not client.is_closed
