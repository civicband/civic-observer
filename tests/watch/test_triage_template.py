"""Rendering tests for the triage queue template and its partials."""

import re
from datetime import date, timedelta

import pytest
from django.template import Context, Template
from django.urls import reverse
from django.utils import timezone

from tests.factories import (
    MeetingHitFactory,
    MentionFactory,
    PlaceFactory,
    PlaceWithMuniFactory,
    ThreadFactory,
    ThreadPlaceFactory,
)
from watch.models import ThreadPlace
from watch.scoring import QUEUE_AGE_OPTIONS


@pytest.mark.django_db
def test_triage_escapes_html_and_highlights_keyword(admin_client):
    thread = ThreadFactory()
    MentionFactory(
        thread=thread,
        keyword="city council",
        snippet_text="<script>alert(1)</script> city council",
    )
    response = admin_client.get(reverse("watch:triage"))
    content = response.content.decode()
    assert "<script>alert(1)</script>" not in content
    assert "&lt;script&gt;" in content
    assert "<mark>city council</mark>" in content


@pytest.mark.django_db
def test_triage_marks_future_agenda(admin_client):
    thread = ThreadFactory()
    place = PlaceWithMuniFactory()
    ThreadPlace.objects.create(
        thread=thread,
        place=place,
        role="subject",
        confidence="high",
        source="subreddit",
    )
    MeetingHitFactory(
        thread=thread,
        place=place,
        meeting_date=timezone.localdate() + timedelta(days=2),
    )
    response = admin_client.get(reverse("watch:triage"), {"thread": thread.id})
    assert b"Future meeting" in response.content


@pytest.mark.django_db
def test_triage_renders_missing_meeting_date_as_dash(admin_client):
    thread = ThreadFactory()
    place = PlaceWithMuniFactory()
    ThreadPlace.objects.create(
        thread=thread,
        place=place,
        role="subject",
        confidence="high",
        source="subreddit",
    )
    hit = MeetingHitFactory(
        thread=thread,
        place=place,
        meeting_date=None,
        meeting_name="Undated session",
    )
    response = admin_client.get(reverse("watch:triage"), {"thread": thread.id})
    content = response.content.decode()
    card = re.search(
        rf'<article[^>]*data-hit="{hit.pk}"[^>]*>.*?</article>', content, re.DOTALL
    )
    assert card is not None
    assert "Undated session" in card.group(0)
    assert "—" in card.group(0)
    assert b"Future meeting" not in response.content


@pytest.mark.django_db
def test_triage_empty_queue_renders_no_card(admin_client):
    response = admin_client.get(reverse("watch:triage"))
    content = response.content.decode()
    assert response.status_code == 200
    assert "No threads to triage." in content
    assert "<article" not in content


@pytest.mark.django_db
def test_triage_never_renders_snippet_html(admin_client):
    thread = ThreadFactory()
    MentionFactory(
        thread=thread,
        keyword="city council",
        snippet_text="safe snippet text",
        snippet_html="<img src=x onerror=alert(1)>SNIPPET-HTML-SENTINEL",
    )
    response = admin_client.get(reverse("watch:triage"), {"thread": thread.id})
    content = response.content.decode()
    assert "safe snippet text" in content
    assert "SNIPPET-HTML-SENTINEL" not in content
    assert "onerror" not in content


@pytest.mark.django_db
def test_triage_page_owns_the_swap_target_but_partial_does_not(admin_client):
    thread = ThreadFactory()
    url = reverse("watch:triage")

    full = admin_client.get(url, {"thread": thread.pk}).content.decode()
    partial = admin_client.get(
        url, {"thread": thread.pk}, HTTP_HX_REQUEST="true"
    ).content.decode()

    assert full.count('id="triage-detail"') == 1
    assert full.count('id="triage-list"') == 1
    assert 'id="triage-detail"' not in partial
    assert partial.lstrip().startswith("<article")


def test_citation_formats_meeting_as_markdown():
    hit = MeetingHitFactory.build(
        meeting_name="City Council",
        meeting_date=date(2024, 1, 15),
        civic_band_url="https://example.com/meetings/1",
    )
    rendered = Template("{% load watch_tags %}{% citation hit %}").render(
        Context({"hit": hit})
    )
    assert rendered == "[City Council (2024-01-15)](https://example.com/meetings/1)"


@pytest.mark.django_db
def test_citation_attribute_escapes_hostile_meeting_name(admin_client):
    thread = ThreadFactory()
    place = PlaceWithMuniFactory()
    ThreadPlace.objects.create(
        thread=thread,
        place=place,
        role="subject",
        confidence="high",
        source="subreddit",
    )
    MeetingHitFactory(
        thread=thread,
        place=place,
        meeting_name='Evil "><img src=x onerror=alert(1)>',
        civic_band_url="https://example.com/meetings/1",
    )
    content = admin_client.get(
        reverse("watch:triage"), {"thread": thread.id}
    ).content.decode()

    match = re.search(r'data-citation="([^"]*)"', content)
    assert match is not None
    citation_value = match.group(1)
    assert "<" not in citation_value
    assert '"' not in citation_value
    assert "&quot;&gt;&lt;img" in citation_value
    assert '"><img' not in content
    assert "<img" not in content


def test_highlight_keywords_does_not_corrupt_entities():
    rendered = Template(
        "{% load watch_tags %}{% highlight_keywords text keyword %}"
    ).render(Context({"text": "AT&T", "keyword": "amp"}))
    assert rendered == "AT&amp;T"
    assert "<mark>" not in rendered


def test_highlight_keywords_marks_keyword_containing_ampersand():
    rendered = Template(
        "{% load watch_tags %}{% highlight_keywords text keyword %}"
    ).render(Context({"text": "AT&T", "keyword": "AT&T"}))
    assert rendered == "<mark>AT&amp;T</mark>"


def test_highlight_keywords_is_case_insensitive_and_escapes():
    rendered = Template(
        "{% load watch_tags %}{% highlight_keywords text keyword %}"
    ).render(
        Context(
            {
                "text": '<b>City Council</b> & "city council"',
                "keyword": "city council",
            }
        )
    )
    assert "<b>" not in rendered
    assert "&lt;b&gt;" in rendered
    assert "&amp;" in rendered
    assert rendered.count("<mark>City Council</mark>") == 1
    assert rendered.count("<mark>city council</mark>") == 1


@pytest.mark.parametrize("url", ["http://example.com/a", "https://example.com/a?b=1"])
def test_external_url_filter_allows_http_and_https(url):
    rendered = Template("{% load watch_tags %}{{ url|external_url }}").render(
        Context({"url": url})
    )
    assert rendered == url


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "ftp://example.com/a",
        "//example.com/a",
        "not a url",
        "",
    ],
)
def test_external_url_filter_drops_other_schemes(url):
    rendered = Template("{% load watch_tags %}{{ url|external_url }}").render(
        Context({"url": url})
    )
    assert rendered == ""


@pytest.mark.django_db
def test_triage_drops_unsafe_mention_url(admin_client):
    thread = ThreadFactory()
    MentionFactory(thread=thread, url="javascript:alert(1)")

    content = admin_client.get(
        reverse("watch:triage"), {"thread": thread.pk}
    ).content.decode()

    assert "javascript:" not in content
    assert "View on Reddit" not in content


@pytest.mark.django_db
def test_triage_drops_unsafe_meeting_hit_url(admin_client):
    thread = ThreadFactory()
    place = PlaceWithMuniFactory()
    ThreadPlace.objects.create(
        thread=thread,
        place=place,
        role="subject",
        confidence="high",
        source="subreddit",
    )
    MeetingHitFactory(
        thread=thread,
        place=place,
        civic_band_url="javascript:alert(1)",
    )

    content = admin_client.get(
        reverse("watch:triage"), {"thread": thread.pk}
    ).content.decode()

    assert 'href="javascript:' not in content
    assert "View page" not in content


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        (
            "reddit:chicoca/abc123",
            "https://www.reddit.com/r/chicoca/comments/abc123/",
        ),
        ("reddit:ChicoCA/ABC123", "https://www.reddit.com/r/ChicoCA/comments/ABC123/"),
        ("https://example.com/a", "https://example.com/a"),
        (
            "https://news.ycombinator.com/item?id=123",
            "https://news.ycombinator.com/item?id=123",
        ),
        ("javascript:alert(1)", ""),
        ("reddit:", ""),
        ("reddit:sub/", ""),
        ("reddit:bad sub/abc", ""),
        ("reddit:sub/abc/def", ""),
        ("", ""),
    ],
)
def test_thread_url_filter_builds_links(key, expected):
    rendered = Template("{% load watch_tags %}{{ key|thread_url }}").render(
        Context({"key": key})
    )
    assert rendered == expected


@pytest.mark.django_db
def test_triage_loads_keyboard_script_without_defer(admin_client):
    """Alpine starts on DOMContentLoaded, so the component must be registered first.

    A `defer`red classic script runs after DOMContentLoaded has fired, so
    Alpine would never see `triageKeyboard` and every shortcut would be dead.
    """
    content = admin_client.get(reverse("watch:triage")).content.decode()

    assert "js/watch-triage.js" in content
    script = content.split("js/watch-triage.js")[0].rsplit("<script", 1)[1]
    assert "defer" not in script
    # It must land after Alpine so Alpine.data exists when it registers.
    assert content.index("alpinejs") < content.index("js/watch-triage.js")


@pytest.mark.django_db
def test_triage_card_offers_reject_for_each_suggestion(admin_client):
    thread = ThreadFactory()
    place = PlaceFactory(name="Suggestedville")
    ThreadPlaceFactory(thread=thread, place=place, role="subject")

    content = admin_client.get(
        reverse("watch:triage"), {"thread": thread.pk}
    ).content.decode()

    assert reverse("watch:place-reject", args=[thread.pk]) in content
    assert "Not a match" in content


@pytest.mark.django_db
def test_triage_card_links_thread_title_to_reddit(admin_client):
    thread = ThreadFactory(key="reddit:test/abc1")

    content = admin_client.get(
        reverse("watch:triage"), {"thread": thread.pk}
    ).content.decode()

    assert "https://www.reddit.com/r/test/comments/abc1/" in content


@pytest.mark.django_db
def test_triage_card_renders_unlinkable_title_as_text(admin_client):
    thread = ThreadFactory(key="not-a-url")

    content = admin_client.get(
        reverse("watch:triage"), {"thread": thread.pk}
    ).content.decode()

    assert "Untitled thread" in content or thread.title in content
    assert "https://example.com/not-a-thread" not in content


@pytest.mark.django_db
def test_triage_renders_age_filter_and_bulk_controls(admin_client):
    thread = ThreadFactory()

    content = admin_client.get(reverse("watch:triage")).content.decode()

    assert 'name="age"' in content
    assert "Older than 1 day" in content
    assert "Older than 7 days" in content
    assert 'id="triage-bulk-form"' in content
    assert 'id="triage-select-all"' in content
    assert 'id="triage-thread-ids"' in content
    assert 'hx-params="not thread_id"' in content
    assert 'name="thread_id"' in content
    assert 'form="triage-bulk-form"' in content
    assert f'value="{thread.pk}"' in content
    assert "Close selected" in content


@pytest.mark.django_db
def test_triage_age_filter_narrows_the_queue(admin_client):
    ten_days = timezone.now() - timedelta(days=10)
    stale = ThreadFactory(first_seen=ten_days, last_seen=ten_days)
    fresh = ThreadFactory()

    response = admin_client.get(reverse("watch:triage"), {"age": "1"})

    assert response.context["queue_count"] == 1
    assert response.context["thread"] == stale
    assert response.context["age_options"] == QUEUE_AGE_OPTIONS
    assert fresh not in response.context["rows"]


@pytest.mark.django_db
def test_triage_bulk_action_carries_active_filters(admin_client):
    ThreadFactory(
        first_seen=timezone.now() - timedelta(days=10),
        last_seen=timezone.now() - timedelta(days=10),
    )

    content = admin_client.get(
        reverse("watch:triage"), {"age": "1", "sort": "oldest"}
    ).content.decode()

    assert reverse("watch:bulk") in content
    assert "age=1&sort=oldest" in content
