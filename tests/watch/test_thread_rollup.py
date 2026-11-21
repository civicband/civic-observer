"""Tests for rolling extracted places up to threads."""

from __future__ import annotations

import pytest
from django.utils import timezone

from tests.factories import (
    MentionFactory,
    PlaceFactory,
    SubredditFactory,
    ThreadFactory,
    ThreadPlaceFactory,
)
from watch.classify import Classification
from watch.enrichment import enrich_thread
from watch.extraction import extract_thread_places
from watch.models import ThreadPlace


@pytest.mark.django_db
def test_post_and_title_places_are_subjects(extraction_places):
    thread = ThreadFactory(title="Chico budget")
    MentionFactory(thread=thread, kind="post", snippet_text="Chico city council voted.")
    extract_thread_places(thread)
    assert ThreadPlace.objects.get(thread=thread, place__name="Chico").role == "subject"


@pytest.mark.django_db
def test_comment_body_pattern_promotes_to_subject(extraction_places):
    thread = ThreadFactory(title="McKinney recall")
    MentionFactory(
        thread=thread,
        kind="comment",
        snippet_text="Huntsville city council should do this.",
    )
    extract_thread_places(thread)
    assert (
        ThreadPlace.objects.get(thread=thread, place__name="Huntsville").role
        == "subject"
    )


@pytest.mark.django_db
def test_enrichment_classifies_unreviewed_subreddit(extraction_places, monkeypatch):
    subreddit = SubredditFactory(name="chicoca", source="gazetteer", reviewed_at=None)
    thread = ThreadFactory(subreddit=subreddit)
    monkeypatch.setattr(
        "watch.enrichment.classify_subreddit",
        lambda name: Classification(
            kind="place",
            place=PlaceFactory(name="Chico"),
            state="CA",
            country="US",
            confidence="high",
            source="gazetteer",
        ),
    )
    monkeypatch.setattr(
        "watch.enrichment.extract_thread_places",
        lambda t: {"subjects": 1, "mentioned": 0, "mentions": 1},
    )
    monkeypatch.setattr("watch.enrichment.refresh_thread_hits", lambda t: 0)
    result = enrich_thread(str(thread.id))
    assert result["status"] == "ok"
    subreddit.refresh_from_db()
    assert subreddit.place.name == "Chico"


@pytest.mark.django_db
def test_passing_comparison_stays_mentioned(extraction_places):
    thread = ThreadFactory(title="Los Angeles rent")
    MentionFactory(
        thread=thread, kind="comment", snippet_text="This is worse than New York."
    )
    extract_thread_places(thread)
    assert (
        ThreadPlace.objects.get(thread=thread, place__name="New York").role
        == "mentioned"
    )


@pytest.mark.django_db
def test_post_title_place_is_subject(extraction_places):
    thread = ThreadFactory()
    MentionFactory(thread=thread, kind="post", post_title="Chico council preview")
    extract_thread_places(thread)
    thread_place = ThreadPlace.objects.get(thread=thread, place__name="Chico")
    assert (thread_place.role, thread_place.source) == ("subject", "post")


@pytest.mark.django_db
def test_subreddit_place_starts_as_subject(extraction_places):
    subreddit = SubredditFactory(place=extraction_places["chico"], confidence="high")
    thread = ThreadFactory(subreddit=subreddit)
    extract_thread_places(thread)
    thread_place = ThreadPlace.objects.get(thread=thread)
    assert (thread_place.place.name, thread_place.role, thread_place.source) == (
        "Chico",
        "subject",
        "subreddit",
    )


@pytest.mark.django_db
def test_merge_prefers_subject_over_mentioned(extraction_places):
    thread = ThreadFactory()
    MentionFactory(
        thread=thread, kind="comment", snippet_text="This is worse than Chico."
    )
    MentionFactory(thread=thread, kind="post", snippet_text="Chico city council voted.")
    extract_thread_places(thread)
    thread_place = ThreadPlace.objects.get(thread=thread, place__name="Chico")
    assert (thread_place.role, thread_place.source) == ("subject", "post")


@pytest.mark.django_db
def test_manual_thread_place_is_preserved(extraction_places):
    thread = ThreadFactory()
    ThreadPlaceFactory(
        thread=thread,
        place=extraction_places["chico"],
        role="mentioned",
        confidence="low",
        source="manual",
    )
    MentionFactory(thread=thread, kind="post", snippet_text="Chico city council voted.")
    extract_thread_places(thread)
    thread_place = ThreadPlace.objects.get(thread=thread, place__name="Chico")
    assert (thread_place.role, thread_place.confidence, thread_place.source) == (
        "mentioned",
        "low",
        "manual",
    )


@pytest.mark.django_db
def test_stale_non_manual_thread_places_are_deleted(extraction_places):
    thread = ThreadFactory()
    ThreadPlaceFactory(
        thread=thread,
        place=extraction_places["beverly_hills"],
        source="comment",
    )
    extract_thread_places(thread)
    assert not ThreadPlace.objects.filter(thread=thread).exists()


@pytest.mark.django_db
def test_enrichment_skips_manual_subreddit_classification(monkeypatch):
    subreddit = SubredditFactory(name="chicoca", source="manual")
    thread = ThreadFactory(subreddit=subreddit)
    monkeypatch.setattr(
        "watch.enrichment.classify_subreddit",
        lambda name: pytest.fail("must not classify a manual subreddit"),
    )
    monkeypatch.setattr("watch.enrichment.refresh_thread_hits", lambda t: 0)
    result = enrich_thread(str(thread.id))
    assert result["status"] == "ok"


@pytest.mark.django_db
def test_enrichment_skips_reviewed_subreddit_classification(monkeypatch):
    subreddit = SubredditFactory(
        name="chicoca", source="gazetteer", reviewed_at=timezone.now()
    )
    thread = ThreadFactory(subreddit=subreddit)
    monkeypatch.setattr(
        "watch.enrichment.classify_subreddit",
        lambda name: pytest.fail("must not reclassify a reviewed subreddit"),
    )
    monkeypatch.setattr("watch.enrichment.refresh_thread_hits", lambda t: 0)
    result = enrich_thread(str(thread.id))
    assert result["status"] == "ok"
