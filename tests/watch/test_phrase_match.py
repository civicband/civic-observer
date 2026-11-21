"""Tests for gazetteer phrase matching in post and comment text."""

from __future__ import annotations

import pytest

from tests.factories import MentionFactory, PlaceFactory, SubredditFactory
from watch.extraction import (
    extract_mention_places,
    extract_phrases,
    resolve_place_phrase,
)
from watch.gazetteer import refresh_aliases
from watch.models import Place, PlaceMention


@pytest.mark.django_db
def test_unanchored_phrase_is_low_confidence(extraction_places):
    results = extract_phrases("A new grant for Chico was announced.", state="CA")
    assert [
        (
            result.place.name,
            result.matched_text,
            result.method,
            result.confidence,
            result.is_subject,
        )
        for result in results
    ] == [("Chico", "Chico", "gazetteer_phrase", "low", False)]


@pytest.mark.django_db
def test_state_prior_breaks_tie(extraction_places):
    results = extract_phrases("Springfield passed a budget.", state="IL")
    assert [
        (result.place.name, result.place.state, result.confidence) for result in results
    ] == [("Springfield", "IL", "low")]


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


@pytest.mark.parametrize(
    "text",
    [
        "Hope this helps.",
        "Mobile homes are cheaper.",
        "Union vote today.",
        "It passed. Hope this helps.",
        "It passed! Mobile homes are cheaper.",
        "It passed? Union vote today.",
    ],
)
@pytest.mark.django_db
def test_sentence_initial_common_word_is_prose(extraction_places, text):
    assert extract_phrases(text, state="CA") == []


@pytest.mark.django_db
def test_common_word_after_newline_is_prose(extraction_places):
    results = extract_phrases("Chico\nHope this helps.", state="CA")
    assert [(result.place.name, result.confidence) for result in results] == [
        ("Chico", "low")
    ]


@pytest.mark.django_db
def test_common_word_mid_sentence_is_a_place(extraction_places):
    results = extract_phrases("We visited Hope last year.", state="AR")
    assert [
        (result.place.name, result.place.state, result.confidence) for result in results
    ] == [("Hope", "AR", "low")]


@pytest.mark.django_db
def test_lowercase_common_word_is_prose(extraction_places):
    assert extract_phrases("we want mobile homes downtown") == []


@pytest.mark.parametrize(
    "text",
    [
        "Chico's budget passed.",
        "A Chico-based nonprofit formed.",
        "The Chico-based nonprofit formed.",
    ],
)
@pytest.mark.django_db
def test_possessive_and_hyphen_keys_normalize(extraction_places, text):
    results = extract_phrases(text, state="CA")
    assert [(result.place.name, result.place.place_type) for result in results] == [
        ("Chico", "city")
    ]


@pytest.mark.parametrize("phrase", ["Chico's", "Chico-based"])
@pytest.mark.django_db
def test_resolve_place_phrase_normalizes_possessive_and_hyphen(
    extraction_places, phrase
):
    assert resolve_place_phrase(phrase, state="CA") == extraction_places["chico"]


@pytest.mark.django_db
def test_unicode_place_names_tokenize():
    place = PlaceFactory(name="Española", state="NM", population=10000)
    refresh_aliases(place)
    results = extract_phrases("Española council met.")
    assert [(result.place.name, result.matched_text) for result in results] == [
        ("Española", "Española")
    ]


@pytest.mark.django_db
def test_mixed_case_brand_fragments_are_not_places():
    for name in ("Phone", "Bay"):
        refresh_aliases(
            PlaceFactory(name=name, state="CA", place_type=Place.PlaceType.CITY)
        )
    assert extract_phrases("iPhone and eBay news.") == []


@pytest.mark.django_db
def test_unanchored_cdp_does_not_roll_up(extraction_places):
    results = extract_phrases("North Richmond needs housing.", state="CA")
    assert [
        (result.place.name, result.place.place_type, result.method)
        for result in results
    ] == [("North Richmond", "cdp", "gazetteer_phrase")]


@pytest.mark.django_db
def test_state_places_are_not_unanchored_matches():
    state = PlaceFactory(
        name="Xylophonia",
        state="",
        place_type=Place.PlaceType.STATE,
    )
    refresh_aliases(state)
    assert extract_phrases("Xylophonia passed a law.") == []


@pytest.mark.parametrize(
    "text",
    [
        "Chico, California announced a grant.",
        "A grant went to Chico.",
        "Chico.",
    ],
)
@pytest.mark.django_db
def test_punctuation_after_name_does_not_block_match(extraction_places, text):
    results = extract_phrases(text, state="CA")
    assert [(result.place.name, result.place.place_type) for result in results] == [
        ("Chico", "city")
    ]


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
    assert [
        (row.place.name, row.method, row.confidence, row.is_subject) for row in first
    ] == [("Chico", "gazetteer_phrase", "low", False)]
    assert [
        (row.place.name, row.method, row.confidence, row.is_subject) for row in second
    ] == [("Chico", "gazetteer_phrase", "low", False)]
    assert PlaceMention.objects.filter(mention=mention).count() == len(first)


@pytest.mark.django_db
def test_subreddit_state_is_used_as_a_prior(extraction_places):
    mention = MentionFactory(snippet_text="Springfield passed a budget.")
    rows = extract_mention_places(mention, SubredditFactory(state="IL"))
    assert [(row.place.name, row.place.state, row.method) for row in rows] == [
        ("Springfield", "IL", "gazetteer_phrase")
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


@pytest.mark.django_db
def test_manual_row_survives_and_nearby_place_is_added(extraction_places):
    mention = MentionFactory(snippet_text="Chico and Springfield passed budgets.")
    PlaceMention.objects.create(
        mention=mention,
        place=extraction_places["chico"],
        matched_text="Chico",
        method=PlaceMention.Method.MANUAL,
        confidence=PlaceMention.Confidence.HIGH,
        is_subject=True,
    )

    rows = extract_mention_places(mention, SubredditFactory(state="IL"))

    assert [
        (row.place.name, row.method, row.confidence, row.is_subject) for row in rows
    ] == [("Springfield", "gazetteer_phrase", "low", False)]
    assert sorted(
        (row.place.name, row.method) for row in mention.place_mentions.all()
    ) == [("Chico", "manual"), ("Springfield", "gazetteer_phrase")]


@pytest.mark.django_db
def test_reextraction_replaces_rows_when_text_and_state_change(extraction_places):
    mention = MentionFactory(snippet_text="Chico needs housing.")
    subreddit = SubredditFactory(state="CA")

    first = extract_mention_places(mention, subreddit)
    assert [(row.place.name, row.place.state) for row in first] == [("Chico", "CA")]

    mention.snippet_text = "Springfield passed a budget."
    mention.save(update_fields=["snippet_text"])
    subreddit.state = "IL"
    subreddit.save(update_fields=["state"])

    second = extract_mention_places(mention, subreddit)

    assert [(row.place.name, row.place.state, row.method) for row in second] == [
        ("Springfield", "IL", "gazetteer_phrase")
    ]
    assert [
        (row.place.name, row.place.state, row.method)
        for row in mention.place_mentions.all()
    ] == [("Springfield", "IL", "gazetteer_phrase")]


@pytest.mark.django_db
def test_llm_rows_are_replaced_by_deterministic_extraction(extraction_places):
    mention = MentionFactory(snippet_text="Chico needs housing.")
    PlaceMention.objects.create(
        mention=mention,
        place=extraction_places["beverly_hills"],
        matched_text="Beverly Hills",
        method=PlaceMention.Method.LLM,
        confidence=PlaceMention.Confidence.HIGH,
        is_subject=False,
    )

    rows = extract_mention_places(mention, SubredditFactory(state="CA"))

    assert [
        (row.place.name, row.method, row.confidence, row.is_subject) for row in rows
    ] == [("Chico", "gazetteer_phrase", "low", False)]
    assert [(row.place.name, row.method) for row in mention.place_mentions.all()] == [
        ("Chico", "gazetteer_phrase")
    ]


@pytest.mark.django_db
def test_llm_row_does_not_suppress_deterministic_match(extraction_places):
    mention = MentionFactory(snippet_text="Chico needs housing.")
    PlaceMention.objects.create(
        mention=mention,
        place=extraction_places["chico"],
        matched_text="Chico",
        method=PlaceMention.Method.LLM,
        confidence=PlaceMention.Confidence.MEDIUM,
        is_subject=True,
    )

    rows = extract_mention_places(mention, SubredditFactory(state="CA"))

    assert [(row.place.name, row.method) for row in rows] == [
        ("Chico", "gazetteer_phrase")
    ]
    assert [(row.place.name, row.method) for row in mention.place_mentions.all()] == [
        ("Chico", "gazetteer_phrase")
    ]
