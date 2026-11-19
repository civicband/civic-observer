import json
from collections import Counter
from pathlib import Path

import pytest

from watch import parsing

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_comment_item():
    page = json.loads((FIXTURES / "f5bot_page_1.json").read_text())
    row = parsing.parse_item(page["items"][0])
    assert row["site"] == "reddit"
    assert row["kind"] == "comment"
    assert row["subreddit"] == "sandiego"
    assert row["post_id"] == "1y059df"
    assert row["comment_id"] == "19svqg3v87cbo"
    assert row["thread_key"] == "reddit:sandiego/1y059df"
    assert row["keyword"] == "board of supervisors"
    assert row["post_title"] == "Synthetic thread 1y059df"
    assert row["published_at"] == "2026-11-19T17:24:12+00:00"
    assert json.loads(row["highlights"]) == ["board of supervisors"]
    assert row["alert_num"] == 138151022
    assert row["author"] == "user0001"


def test_entities_unescaped():
    row = parsing.parse_item(
        {
            "id": "F5BOT-alert-1-1",
            "url": "https://www.reddit.com/r/treelaw/comments/abc/c/def",
            "title": "planning commission - Reddit Comments - Synthetic thread abc",
            "content_html": "&gt; trees &quot;in violation of a Beverly Hills <strong>Planning Commission</strong>",
            "date_published": "2026-11-19T11:19:08-06:00",
            "tags": ["planning commission", "Reddit Comments"],
        }
    )
    assert row["snippet_text"].startswith('> trees "in violation of a Beverly Hills')
    assert json.loads(row["highlights"]) == ["planning commission"]


def test_post_and_canonical_comment_urls():
    base = {
        "id": "F5BOT-alert-1-2",
        "title": "city council - Reddit Posts - Synthetic",
        "content_html": "x",
        "date_published": "2026-11-19T11:19:08-06:00",
        "tags": ["city council", "Reddit Posts"],
    }
    post = parsing.parse_item(
        dict(base, url="https://www.reddit.com/r/ChicoCA/comments/abc/planning_thing/")
    )
    assert (post["subreddit"], post["post_id"], post["comment_id"], post["kind"]) == (
        "chicoca",
        "abc",
        None,
        "post",
    )
    comment = parsing.parse_item(
        dict(
            base,
            tags=["city council", "Reddit Comments"],
            url="https://www.reddit.com/r/Seattle/comments/abc/slug/def123/",
        )
    )
    assert (comment["post_id"], comment["comment_id"]) == ("abc", "def123")


def test_hn_item():
    row = parsing.parse_item(
        {
            "id": "F5BOT-alert-1-3",
            "url": "https://news.ycombinator.com/item?id=4242",
            "title": "city council - Hacker News Comments - Synthetic",
            "content_html": "x",
            "date_published": "2026-11-19T11:19:08-06:00",
            "tags": ["city council", "Hacker News Comments"],
        }
    )
    assert (row["site"], row["thread_key"], row["subreddit"]) == (
        "hackernews",
        "hn:4242",
        None,
    )


def test_title_without_expected_prefix():
    assert parsing.strip_title_prefix("Just a title", "x", "y") == "Just a title"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("not-a-date", "not-a-date"),
        ("2026-11-19T11:19:08", "2026-11-19T11:19:08+00:00"),
        ("2026-11-19T11:19:08Z", "2026-11-19T11:19:08+00:00"),
        ("2026-11-19T11:19:08-06:00", "2026-11-19T17:19:08+00:00"),
    ],
)
def test_to_utc_iso(value, expected):
    assert parsing.to_utc_iso(value) == expected


@pytest.mark.parametrize(
    ("host", "site"),
    [
        ("WWW.REDDIT.COM", "reddit"),
        ("redd.it", "reddit"),
        ("news.ycombinator.com", "hackernews"),
        ("lobste.rs", "lobsters"),
        ("example.com", "other"),
    ],
)
def test_classify_site(host, site):
    assert parsing.classify_site(host) == site


def test_fixture_pages_preserve_thread_distribution():
    pages = [
        json.loads((FIXTURES / f"f5bot_page_{i}.json").read_text()) for i in (1, 2)
    ]
    assert [len(page["items"]) for page in pages] == [50, 38]
    assert [page["next_url"] for page in pages] == ["fixture:page-2", None]
    rows = [parsing.parse_item(item) for page in pages for item in page["items"]]
    threads = Counter(row["thread_key"] for row in rows)
    assert len(threads) == 69
    assert threads["reddit:losangeles/1xzzze7"] == 16
    assert sum(row["subreddit"] == "synthetic_sub_01" for row in rows) == 1


def test_fixture_fields_are_synthetic_and_have_no_feed_credentials():
    items = []
    for i in (1, 2):
        text = (FIXTURES / f"f5bot_page_{i}.json").read_text()
        assert "a=" not in text
        assert "h=" not in text
        page = json.loads(text)
        assert not {"feed_url", "_prev_url", "_processing_time_seconds"} & page.keys()
        items.extend(page["items"])

    for index, item in enumerate(items, 1):
        keyword, source_tag = item["tags"]
        post_id = item["url"].split("/")[6].lower()
        assert item["username"] == f"user{index:04d}"
        assert item["title"] == f"{keyword} - {source_tag} - Synthetic thread {post_id}"
        assert item["content_html"] == (
            f"Synthetic <strong>{keyword}</strong> alert for thread {post_id}."
        )
