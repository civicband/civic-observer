"""Tests for the weekly summary email and the per-keyword yield report."""

from __future__ import annotations

import json
from datetime import timedelta
from io import StringIO

import pytest
from django.core import mail
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from tests.factories import (
    MentionFactory,
    PlaceFactory,
    PlaceWithMuniFactory,
    ThreadFactory,
    ThreadPlaceFactory,
)
from watch.models import Thread

SUMMARY_SUBJECT = "CivicBand Watch: weekly summary"


def _age_thread(thread: Thread, *, days: int = 0, minutes: int = 0) -> None:
    """Move ``modified`` into the past; ``auto_now`` overrides it on save."""
    Thread.objects.filter(pk=thread.pk).update(
        modified=timezone.now() - timedelta(days=days, minutes=minutes)
    )


def _keyword_dataset() -> tuple[Thread, Thread]:
    """Create two keywords across covered, uncovered, and replied threads."""
    covered = ThreadFactory(status="replied")
    ThreadPlaceFactory(
        thread=covered,
        place=PlaceWithMuniFactory(name="Springfield"),
        role="subject",
    )
    # A second role row multiplies the join; distinct counts must ignore it.
    ThreadPlaceFactory(
        thread=covered,
        place=PlaceWithMuniFactory(name="Shelbyville"),
        role="mentioned",
    )
    MentionFactory(keyword="city council", thread=covered)
    MentionFactory(keyword="city council", thread=covered)
    uncovered = ThreadFactory(status="new")
    MentionFactory(keyword="city council", thread=uncovered)
    MentionFactory(keyword="budget", thread=ThreadFactory())
    return covered, uncovered


@pytest.mark.django_db
def test_weekly_summary_sends_counts(settings):
    settings.WATCH_SUMMARY_EMAIL = ["ops@example.com"]
    _age_thread(ThreadFactory(status="replied"))
    _age_thread(ThreadFactory(status="replied"))
    _age_thread(ThreadFactory(status="dismissed"))
    _age_thread(ThreadFactory(status="replied"), days=10)
    _age_thread(ThreadFactory(status="dismissed"), days=10)
    ThreadPlaceFactory(
        thread=ThreadFactory(status="new"),
        place=PlaceFactory(name="Springfield", muni=None),
        role="subject",
    )

    call_command("watch_weekly_summary")

    assert len(mail.outbox) == 1
    message = mail.outbox[0]
    assert message.subject == SUMMARY_SUBJECT
    assert message.to == ["ops@example.com"]
    assert "Replied in the last 7 days: 2" in message.body
    assert "Dismissed in the last 7 days: 1" in message.body
    assert "New uncovered subject places in the backlog: 1" in message.body


@pytest.mark.django_db
def test_weekly_summary_caps_replied_threads_at_twenty(settings):
    settings.WATCH_SUMMARY_EMAIL = ["ops@example.com"]
    place = PlaceFactory(name="Springfield", muni=None)
    newest = None
    for index in range(22):
        thread = ThreadFactory(title=f"Weekly thread {index:02d}", status="replied")
        _age_thread(thread, minutes=index + 1)
        if index == 0:
            newest = thread
            ThreadPlaceFactory(thread=thread, place=place, role="subject")

    call_command("watch_weekly_summary")

    body = mail.outbox[0].body
    assert body.count("/triage/?thread=") == 20
    assert body.index("Weekly thread 00") < body.index("Weekly thread 01")
    assert "Weekly thread 19" in body
    assert "Weekly thread 20" not in body
    assert "Weekly thread 21" not in body
    assert newest is not None
    assert f"/triage/?thread={newest.pk}" in body
    assert "Place: Springfield" in body


@pytest.mark.django_db
def test_weekly_summary_names_first_place_by_name_or_no_place(settings):
    settings.WATCH_SUMMARY_EMAIL = ["ops@example.com"]
    covered = ThreadFactory(title="Has a place", status="replied")
    springfield = PlaceFactory(name="Springfield", muni=None)
    aardvark = PlaceFactory(name="Aardvark", muni=None)
    ThreadPlaceFactory(thread=covered, place=springfield, role="subject")
    ThreadPlaceFactory(thread=covered, place=aardvark, role="subject")
    empty = ThreadFactory(title="Has no place", status="replied")

    call_command("watch_weekly_summary")

    body = mail.outbox[0].body
    assert f"/triage/?thread={covered.pk}" in body
    assert "Place: Aardvark" in body
    assert "Place: Springfield" not in body
    assert f"/triage/?thread={empty.pk}" in body
    assert "no place" in body


@pytest.mark.django_db
def test_weekly_summary_prefetches_thread_places(
    settings, django_assert_max_num_queries
):
    settings.WATCH_SUMMARY_EMAIL = ["ops@example.com"]
    place = PlaceFactory(name="Springfield", muni=None)
    for index in range(10):
        thread = ThreadFactory(title=f"Thread {index}", status="replied")
        ThreadPlaceFactory(thread=thread, place=place, role="subject")

    with django_assert_max_num_queries(8):
        call_command("watch_weekly_summary")

    assert "Place: Springfield" in mail.outbox[0].body


@pytest.mark.django_db
def test_weekly_summary_omits_snippets_and_feed_url(settings):
    settings.WATCH_SUMMARY_EMAIL = ["ops@example.com"]
    settings.F5BOT_FEED_URL = "https://f5bot.com/secret-access-hash"
    thread = ThreadFactory(title="Budget hearing", status="replied")
    MentionFactory(
        thread=thread,
        snippet_text="SECRET SNIPPET TEXT",
        snippet_html="<p>SECRET SNIPPET TEXT</p>",
    )

    call_command("watch_weekly_summary")

    body = mail.outbox[0].body
    assert "Budget hearing" in body
    assert "SECRET SNIPPET" not in body
    assert "f5bot.com" not in body
    assert "secret-access-hash" not in body


@pytest.mark.django_db
def test_weekly_summary_skips_when_no_recipients(settings):
    settings.WATCH_SUMMARY_EMAIL = []
    ThreadFactory(status="replied")

    call_command("watch_weekly_summary")

    assert mail.outbox == []


@pytest.mark.django_db
def test_keyword_report_text_includes_keyword(capsys):
    _keyword_dataset()

    call_command("watch_keyword_report")

    output = capsys.readouterr().out
    assert "city council" in output
    assert "budget" in output
    assert "mentions" in output


@pytest.mark.django_db
def test_keyword_report_json_counts_mentions_threads_covered_and_replied():
    _keyword_dataset()
    stdout = StringIO()

    call_command("watch_keyword_report", "--json", stdout=stdout)

    rows = json.loads(stdout.getvalue())
    assert rows == [
        {
            "keyword": "budget",
            "mentions": 1,
            "threads": 1,
            "covered_threads": 0,
            "replied_threads": 0,
        },
        {
            "keyword": "city council",
            "mentions": 3,
            "threads": 2,
            "covered_threads": 1,
            "replied_threads": 1,
        },
    ]


@pytest.mark.django_db
def test_keyword_report_filters_mentions_by_days():
    MentionFactory(keyword="recent", published_at=timezone.now() - timedelta(days=2))
    MentionFactory(keyword="stale", published_at=timezone.now() - timedelta(days=10))
    MentionFactory(keyword="ancient", published_at=timezone.now() - timedelta(days=40))

    def keywords(*args: str) -> list[str]:
        stdout = StringIO()
        call_command("watch_keyword_report", "--json", *args, stdout=stdout)
        return [row["keyword"] for row in json.loads(stdout.getvalue())]

    assert keywords() == ["recent", "stale"]
    assert keywords("--days", "7") == ["recent"]
    assert keywords("--days", "60") == ["ancient", "recent", "stale"]


@pytest.mark.django_db
def test_keyword_report_excludes_blank_keywords():
    MentionFactory(keyword="")
    MentionFactory(keyword="   ")
    MentionFactory(keyword="real keyword")
    stdout = StringIO()

    call_command("watch_keyword_report", "--json", stdout=stdout)

    rows = json.loads(stdout.getvalue())
    assert [row["keyword"] for row in rows] == ["real keyword"]


@pytest.mark.django_db
@pytest.mark.parametrize("days", ["0", "-3"])
def test_keyword_report_rejects_non_positive_days(days):
    with pytest.raises(CommandError, match="--days must be a positive integer"):
        call_command("watch_keyword_report", "--days", days)
