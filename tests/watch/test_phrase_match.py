"""Tests for gazetteer phrase matching in post and comment text."""

from __future__ import annotations

import pytest

from tests.factories import MentionFactory, PlaceFactory, SubredditFactory
from watch.extraction import extract_mention_places, extract_phrases
from watch.gazetteer import refresh_aliases
from watch.models import PlaceMention


@pytest.mark.django_db
def test_unanchored_phrase_is_low_confidence(extraction_places):
    results = extract_phrases("A new grant for Chico was announced.", state="CA")
    assert results[0].place.name == "Chico"
    assert results[0].confidence == "low"
    assert results[0].method == "gazetteer_phrase"
    assert results[0].is_subject is False


@pytest.mark.django_db
def test_state_prior_breaks_tie(extraction_places):
    results = extract_phrases("Springfield passed a budget.", state="IL")
    assert [result.place.state for result in results] == ["IL"]


@pytest.mark.django_db
def test_without_a_state_prior_ambiguous_city_is_skipped(extraction_places):
    assert extract_phrases("Springfield passed a budget.") == []


@pytest.mark.django_db
def test_multiword_place_matches_as_one_phrase(extraction_places):
    results = extract_phrases("The Beverly Hills council met.", state="CA")
    assert [(result.place.name, result.matched_text) for result in results] == [
        ("Beverly Hills", "Beverly Hills")
    ]


@pytest.mark.django_db
def test_longest_phrase_wins_over_its_subphrase(extraction_places):
    results = extract_phrases("Riverside County supervisors met.", state="CA")
    assert [(result.place.name, result.place.place_type) for result in results] == [
        ("Riverside County", "county")
    ]


@pytest.mark.django_db
def test_phrase_match_caps_at_four_words():
    place = PlaceFactory(name="Four Word Place Name", state="CA", population=1000)
    refresh_aliases(place)
    results = extract_phrases("Four Word Place Name council")
    assert [(result.place.name, result.matched_text) for result in results] == [
        ("Four Word Place Name", "Four Word Place Name")
    ]


@pytest.mark.django_db
def test_five_word_aliases_are_not_matched():
    place = PlaceFactory(name="One Two Three Four Five", state="CA", population=1000)
    refresh_aliases(place)
    assert extract_phrases("One Two Three Four Five") == []


@pytest.mark.django_db
def test_lowercase_common_word_is_prose(extraction_places):
    assert extract_phrases("we want mobile homes downtown") == []
    results = extract_phrases("Mobile homes are cheaper.", state="AL")
    assert [result.place.name for result in results] == ["Mobile"]


@pytest.mark.django_db
def test_duplicate_place_matches_collapse(extraction_places):
    results = extract_phrases("Chico and Chico again.", state="CA")
    assert [(result.place.name, result.is_subject) for result in results] == [
        ("Chico", False)
    ]


@pytest.mark.django_db
def test_phrase_matching_queries_aliases_once(
    extraction_places, django_assert_num_queries
):
    with django_assert_num_queries(1):
        extract_phrases("Chico and Springfield met.", state="CA")


@pytest.mark.django_db
def test_mention_extraction_is_idempotent(extraction_places):
    mention = MentionFactory(snippet_text="Chico needs housing.")
    subreddit = SubredditFactory(state="CA")
    first = extract_mention_places(mention, subreddit)
    second = extract_mention_places(mention, subreddit)
    assert [(p.place_id, p.method) for p in first] == [
        (p.place_id, p.method) for p in second
    ]
    assert PlaceMention.objects.filter(mention=mention).count() == len(first)


@pytest.mark.django_db
def test_subreddit_state_is_used_as_a_prior(extraction_places):
    mention = MentionFactory(snippet_text="Springfield passed a budget.")
    rows = extract_mention_places(mention, SubredditFactory(state="IL"))
    assert [(row.place.name, row.place.state) for row in rows] == [
        ("Springfield", "IL")
    ]


@pytest.mark.django_db
def test_body_pattern_beats_the_same_phrase_match(extraction_places):
    mention = MentionFactory(snippet_text="Chico City Council voted on housing.")
    rows = extract_mention_places(mention, SubredditFactory(state="CA"))
    assert [
        (row.place.name, row.method, row.confidence, row.is_subject) for row in rows
    ] == [("Chico", "body_pattern", "high", True)]


@pytest.mark.django_db
def test_manual_rows_survive_reextraction(extraction_places):
    mention = MentionFactory(snippet_text="Chico needs housing.")
    PlaceMention.objects.create(
        mention=mention,
        place=extraction_places["chico"],
        matched_text="Chico",
        method=PlaceMention.Method.MANUAL,
        confidence=PlaceMention.Confidence.HIGH,
        is_subject=True,
    )

    rows = extract_mention_places(mention, SubredditFactory(state="CA"))

    assert rows == []
    assert [(row.place.name, row.method) for row in mention.place_mentions.all()] == [
        ("Chico", "manual")
    ]
