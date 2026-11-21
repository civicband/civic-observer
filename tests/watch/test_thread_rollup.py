"""Tests for rolling extracted places up to threads."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest
from django.utils import timezone

from tests.factories import (
    MentionFactory,
    PlaceWithMuniFactory,
    SubredditFactory,
    ThreadFactory,
    ThreadPlaceFactory,
)
from watch.classify import Classification
from watch.enrichment import enrich_thread
from watch.extraction import extract_thread_places
from watch.gazetteer import refresh_aliases
from watch.llm import LLMPlace, LLMResult
from watch.models import MeetingHit, PlaceMention, ThreadPlace


@pytest.mark.django_db
def test_post_body_place_is_subject(extraction_places):
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
@pytest.mark.parametrize("kind", ["post", "comment"])
@pytest.mark.parametrize(
    ("is_subject", "role"), [(True, "subject"), (False, "mentioned")]
)
def test_manual_place_mentions_honor_is_subject(
    extraction_places, kind, is_subject, role
):
    thread = ThreadFactory()
    mention = MentionFactory(thread=thread, kind=kind, snippet_text="No place here.")
    PlaceMention.objects.create(
        mention=mention,
        place=extraction_places["chico"],
        matched_text="Chico",
        method=PlaceMention.Method.MANUAL,
        confidence=PlaceMention.Confidence.HIGH,
        is_subject=is_subject,
    )

    extract_thread_places(thread)

    thread_place = ThreadPlace.objects.get(thread=thread, place__name="Chico")
    assert (thread_place.role, thread_place.source) == (role, "manual")


@pytest.mark.django_db
def test_manual_source_wins_over_deterministic_source(extraction_places):
    thread = ThreadFactory()
    manual_mention = MentionFactory(
        thread=thread, kind="post", snippet_text="No place here."
    )
    PlaceMention.objects.create(
        mention=manual_mention,
        place=extraction_places["chico"],
        matched_text="Chico",
        method=PlaceMention.Method.MANUAL,
        confidence=PlaceMention.Confidence.LOW,
        is_subject=False,
    )
    MentionFactory(thread=thread, kind="post", snippet_text="Chico city council voted.")

    extract_thread_places(thread)

    thread_place = ThreadPlace.objects.get(thread=thread, place__name="Chico")
    assert (thread_place.role, thread_place.confidence, thread_place.source) == (
        "subject",
        "high",
        "manual",
    )


@pytest.mark.django_db
@pytest.mark.parametrize("kind", ["post", "comment"])
@pytest.mark.parametrize(
    ("is_subject", "role"), [(True, "subject"), (False, "mentioned")]
)
def test_llm_place_mentions_honor_is_subject(
    settings, monkeypatch, extraction_places, kind, is_subject, role
):
    settings.WATCH_LLM_ENABLED = True
    settings.WATCH_LLM_BASE_URL = "https://llm.example/v1"
    settings.WATCH_LLM_MODEL = "small"
    thread = ThreadFactory(subreddit=SubredditFactory(kind="general"))
    mention = MentionFactory(thread=thread, kind=kind, snippet_text="Hope is nearby.")
    monkeypatch.setattr(
        "watch.extraction.classify_with_llm",
        lambda thread: LLMResult(
            places=[
                LLMPlace(
                    name="Hope",
                    state="AR",
                    role="subject" if is_subject else "mentioned",
                )
            ],
            is_us=True,
            topic="",
        ),
    )

    extract_thread_places(thread)

    place_mention = PlaceMention.objects.get(mention=mention, method="llm")
    assert place_mention.is_subject is is_subject
    thread_place = ThreadPlace.objects.get(thread=thread, place__name="Hope")
    expected_source = "post" if kind == "post" else "comment"
    assert (thread_place.role, thread_place.source) == (role, expected_source)


@pytest.mark.django_db
def test_enrichment_classifies_unreviewed_subreddit(extraction_places, monkeypatch):
    subreddit = SubredditFactory(name="chicoca", source="gazetteer", reviewed_at=None)
    thread = ThreadFactory(subreddit=subreddit)
    monkeypatch.setattr(
        "watch.enrichment.classify_subreddit",
        lambda name: Classification(
            kind="place",
            place=extraction_places["chico"],
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
    assert subreddit.place_id == extraction_places["chico"].id
    assert subreddit.classified_at is not None


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
def test_comment_only_thread_uses_title_place_as_subject(extraction_places):
    thread = ThreadFactory(title="Chico budget")
    MentionFactory(
        thread=thread, kind="comment", snippet_text="This is worse than New York."
    )

    extract_thread_places(thread)

    chico = ThreadPlace.objects.get(thread=thread, place__name="Chico")
    assert (chico.role, chico.source) == ("subject", "post")
    new_york = ThreadPlace.objects.get(thread=thread, place__name="New York")
    assert new_york.role == "mentioned"


@pytest.mark.django_db
def test_thread_title_and_post_title_do_not_duplicate(extraction_places):
    thread = ThreadFactory(title="Chico budget")
    MentionFactory(
        thread=thread,
        kind="post",
        post_title="Chico council preview",
        snippet_text="Chico city council voted.",
    )

    result = extract_thread_places(thread)

    assert result == {"subjects": 1, "mentioned": 0, "mentions": 1}
    assert ThreadPlace.objects.filter(thread=thread, place__name="Chico").count() == 1


@pytest.mark.django_db
def test_comment_post_title_is_ignored(extraction_places):
    thread = ThreadFactory()
    MentionFactory(
        thread=thread,
        kind="comment",
        post_title="Beverly Hills budget",
        snippet_text="This is worse than New York.",
    )
    extract_thread_places(thread)
    assert not ThreadPlace.objects.filter(
        thread=thread, place__name="Beverly Hills"
    ).exists()
    assert (
        ThreadPlace.objects.get(thread=thread, place__name="New York").role
        == "mentioned"
    )


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
def test_merge_prefers_highest_confidence_within_a_role(extraction_places):
    subreddit = SubredditFactory(place=extraction_places["chico"], confidence="low")
    thread = ThreadFactory(subreddit=subreddit)
    MentionFactory(thread=thread, kind="post", snippet_text="Chico city council voted.")
    extract_thread_places(thread)
    thread_place = ThreadPlace.objects.get(thread=thread, place__name="Chico")
    assert (
        thread_place.role,
        thread_place.confidence,
        thread_place.source,
    ) == ("subject", "high", "post")


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
def test_stale_manual_place_survives_alongside_a_live_candidate(extraction_places):
    thread = ThreadFactory()
    stale = ThreadPlaceFactory(
        thread=thread,
        place=extraction_places["beverly_hills"],
        role="mentioned",
        source="manual",
    )
    MentionFactory(thread=thread, kind="post", snippet_text="Chico city council voted.")
    extract_thread_places(thread)
    stale.refresh_from_db()
    assert (stale.place.name, stale.role, stale.source) == (
        "Beverly Hills",
        "mentioned",
        "manual",
    )
    assert ThreadPlace.objects.filter(
        thread=thread, place__name="Chico", role="subject", source="post"
    ).exists()


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
def test_extract_thread_places_is_idempotent(extraction_places):
    thread = ThreadFactory()
    MentionFactory(thread=thread, kind="post", snippet_text="Chico city council voted.")

    first = extract_thread_places(thread)
    first_place_ids = set(
        ThreadPlace.objects.filter(thread=thread).values_list("pk", flat=True)
    )
    first_mention_ids = set(
        PlaceMention.objects.filter(mention__thread=thread).values_list("pk", flat=True)
    )

    second = extract_thread_places(thread)

    assert first == second == {"subjects": 1, "mentioned": 0, "mentions": 1}
    assert (
        set(ThreadPlace.objects.filter(thread=thread).values_list("pk", flat=True))
        == first_place_ids
    )
    second_mention_ids = set(
        PlaceMention.objects.filter(mention__thread=thread).values_list("pk", flat=True)
    )
    assert len(second_mention_ids) == len(first_mention_ids)
    # PlaceMention rows are replaced wholesale on re-extraction, so their
    # primary keys churn even though the row count and places are stable.
    assert second_mention_ids != first_mention_ids


@pytest.mark.django_db
def test_enrichment_classifies_before_extracting(extraction_places, monkeypatch):
    subreddit = SubredditFactory(name="chico", source="gazetteer", reviewed_at=None)
    thread = ThreadFactory(subreddit=subreddit)
    monkeypatch.setattr("watch.enrichment.refresh_thread_hits", lambda t: 0)

    result = enrich_thread(str(thread.id))

    subreddit.refresh_from_db()
    assert subreddit.place_id == extraction_places["chico"].id
    thread_place = ThreadPlace.objects.get(
        thread=thread, place_id=extraction_places["chico"].id
    )
    assert (thread_place.role, thread_place.source) == ("subject", "subreddit")
    assert result["subjects"] == 1


@pytest.mark.django_db
@pytest.mark.parametrize(
    "source",
    ["manual", "wikidata", "text_evidence", "llm"],
)
def test_enrichment_skips_trusted_subreddit_sources(monkeypatch, source):
    subreddit = SubredditFactory(name="chicoca", source=source)
    thread = ThreadFactory(subreddit=subreddit)
    monkeypatch.setattr(
        "watch.enrichment.classify_subreddit",
        lambda name: pytest.fail("must not classify a trusted source"),
    )
    monkeypatch.setattr("watch.enrichment.refresh_thread_hits", lambda t: 0)

    result = enrich_thread(str(thread.id))

    subreddit.refresh_from_db()
    assert result["status"] == "ok"
    assert subreddit.classified_at is None


@pytest.mark.django_db
def test_enrichment_skips_already_classified_subreddit(monkeypatch):
    classified_at = timezone.now()
    subreddit = SubredditFactory(
        name="chicoca", source="gazetteer", classified_at=classified_at
    )
    thread = ThreadFactory(subreddit=subreddit)
    monkeypatch.setattr(
        "watch.enrichment.classify_subreddit",
        lambda name: pytest.fail("must not reclassify an already-classified row"),
    )
    monkeypatch.setattr("watch.enrichment.refresh_thread_hits", lambda t: 0)

    result = enrich_thread(str(thread.id))

    subreddit.refresh_from_db()
    assert result["status"] == "ok"
    assert subreddit.classified_at == classified_at


@pytest.mark.django_db
def test_enrichment_creates_extracted_place_and_meeting_hit(monkeypatch):
    place = PlaceWithMuniFactory(name="Chico", state="CA")
    refresh_aliases(place)
    thread = ThreadFactory(title="Chico budget")
    MentionFactory(thread=thread, kind="post", snippet_text="Chico city council voted.")
    results = [
        {
            "id": "page-0",
            "snippet": "budget",
            "meeting_name": "CityCouncil",
            "meeting_date": date(2024, 1, 15),
            "document_type": "agenda",
            "subdomain": "chico",
            "civic_band_table_name": "agendas",
        }
    ]
    monkeypatch.setattr(
        "searches.search_backends.get_search_backend",
        lambda: SimpleNamespace(
            search_with_cache=lambda **kwargs: (results, len(results))
        ),
    )

    result = enrich_thread(str(thread.id))

    thread_place = ThreadPlace.objects.get(thread=thread, place=place)
    assert (thread_place.role, thread_place.source) == ("subject", "post")
    assert MeetingHit.objects.filter(
        thread=thread, place=place, page_id="page-0"
    ).exists()
    assert result["hits"] == 1


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
