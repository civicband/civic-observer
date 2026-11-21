"""Tests for collapsing duplicate mentions inside one thread."""

import json
from pathlib import Path

import pytest

from tests.factories import MentionFactory, ThreadFactory
from watch import parsing
from watch.ingest import ingest_page, is_duplicate_mention
from watch.models import Mention, Thread

FIXTURES = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIXTURES / name).read_text())


def _row(*, alert_id="F5BOT-alert-1-99", author="user1", text="Same text"):
    return parsing.parse_item(
        {
            "id": alert_id,
            "url": "https://www.reddit.com/r/test/comments/abc/c/def",
            "title": "city council - Reddit Comments - Synthetic thread abc",
            "content_html": text,
            "date_published": "2026-11-19T11:19:08-06:00",
            "tags": ["city council", "Reddit Comments"],
            "username": author,
        }
    )


@pytest.mark.django_db
def test_same_author_same_text_in_thread_is_skipped():
    thread = ThreadFactory()
    MentionFactory(thread=thread, author="user1", snippet_text="Same text")
    row = parsing.parse_item(
        {
            "id": "F5BOT-alert-1-2",
            "url": "https://www.reddit.com/r/test/comments/abc/c/def",
            "title": "city council - Reddit Comments - Synthetic thread abc",
            "content_html": "Same text",
            "date_published": "2026-11-19T11:19:08-06:00",
            "tags": ["city council", "Reddit Comments"],
            "username": "user1",
        }
    )
    assert is_duplicate_mention(row, thread) is True


@pytest.mark.django_db
def test_case_and_whitespace_are_normalized():
    thread = ThreadFactory()
    MentionFactory(thread=thread, author="User1", snippet_text="Same   TEXT\nhere")
    row = _row(author="  user1 ", text="same text here")
    assert is_duplicate_mention(row, thread) is True


@pytest.mark.django_db
def test_different_author_same_text_stays():
    thread = ThreadFactory()
    MentionFactory(thread=thread, author="user1", snippet_text="Same text")
    assert is_duplicate_mention(_row(author="user2"), thread) is False


@pytest.mark.django_db
def test_same_author_different_text_stays():
    thread = ThreadFactory()
    MentionFactory(thread=thread, author="user1", snippet_text="Same text")
    assert is_duplicate_mention(_row(text="Different text"), thread) is False


@pytest.mark.django_db
@pytest.mark.parametrize("author", [None, "", "   "])
def test_empty_author_never_collapses(author):
    thread = ThreadFactory()
    MentionFactory(thread=thread, author="", snippet_text="Same text")
    assert is_duplicate_mention(_row(author=author), thread) is False


@pytest.mark.django_db
@pytest.mark.parametrize("text", ["", "   \n "])
def test_empty_text_never_collapses(text):
    thread = ThreadFactory()
    MentionFactory(thread=thread, author="user1", snippet_text="")
    assert is_duplicate_mention(_row(text=text), thread) is False


@pytest.mark.django_db
def test_ingest_collapses_identical_text():
    page = load("f5bot_page_1.json")
    page["items"].append(dict(page["items"][0]))
    page["items"][-1]["id"] = "F5BOT-alert-1-duplicate"
    seen, new, skipped = ingest_page(page)
    assert (seen, new, skipped) == (0, 50, 1)
    assert Mention.objects.filter(id="F5BOT-alert-1-duplicate").count() == 0
    assert Mention.objects.count() == 50
    thread = Thread.objects.get(key="reddit:sandiego/1y059df")
    assert thread.mention_count == 1
