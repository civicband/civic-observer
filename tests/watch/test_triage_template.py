"""Rendering tests for the triage queue template and its partials."""

from datetime import date, timedelta

import pytest
from django.template import Context, Template
from django.urls import reverse
from django.utils import timezone

from tests.factories import (
    MeetingHitFactory,
    MentionFactory,
    PlaceWithMuniFactory,
    ThreadFactory,
)
from watch.models import ThreadPlace


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
    MeetingHitFactory(
        thread=thread,
        place=place,
        meeting_date=None,
        meeting_name="Undated session",
    )
    response = admin_client.get(reverse("watch:triage"), {"thread": thread.id})
    content = response.content.decode()
    assert "Undated session" in content
    assert "—" in content
    assert b"Future meeting" not in response.content


@pytest.mark.django_db
def test_triage_empty_queue_renders_no_card(admin_client):
    response = admin_client.get(reverse("watch:triage"))
    content = response.content.decode()
    assert response.status_code == 200
    assert "No threads to triage." in content


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
