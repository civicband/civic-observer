"""Tests for body-pattern extraction and phrase resolution."""

from __future__ import annotations

import pytest

from watch.extraction import (
    BODY_PATTERNS,
    COMMON_WORD_NAMES,
    ExtractedPlace,
    extract_body_patterns,
    resolve_place_phrase,
)


@pytest.mark.django_db
def test_city_council_pattern_resolves_subject(extraction_places):
    results = extract_body_patterns(
        "The Beverly Hills Planning Commission meets Tuesday."
    )
    assert [(r.place.name, r.is_subject) for r in results] == [("Beverly Hills", True)]


@pytest.mark.django_db
def test_county_board_resolves_county(extraction_places):
    results = extract_body_patterns("Riverside County Board of Supervisors voted yes.")
    assert results[0].place.place_type == "county"
    assert results[0].place.name == "Riverside"


@pytest.mark.django_db
def test_cdp_rolls_up_to_county(extraction_places):
    results = extract_body_patterns("The North Richmond Planning Commission met.")
    assert results[0].place.place_type == "county"


@pytest.mark.django_db
def test_common_word_place_requires_capitalization(extraction_places):
    assert extract_body_patterns("we need mobile city council reform") == []
    assert extract_body_patterns("Mobile City Council voted")[0].place.name == "Mobile"


@pytest.mark.django_db
def test_city_of_phrase_resolves_the_named_type(extraction_places):
    results = extract_body_patterns("The City of Riverside approved the budget.")
    assert [(r.place.name, r.place.place_type) for r in results] == [
        ("Riverside", "city")
    ]


@pytest.mark.django_db
def test_county_of_phrase_resolves_the_county(extraction_places):
    results = extract_body_patterns("The County of Riverside issued a notice.")
    assert [(r.place.name, r.place.place_type) for r in results] == [
        ("Riverside", "county")
    ]


@pytest.mark.django_db
def test_board_of_supervisors_requires_a_county(extraction_places):
    results = extract_body_patterns("Riverside Board of Supervisors voted yes.")
    assert [r.place.place_type for r in results] == ["county"]


@pytest.mark.django_db
def test_state_prior_breaks_a_name_tie(extraction_places):
    place = resolve_place_phrase("Springfield", state="IL")
    assert place is not None
    assert place.state == "IL"


@pytest.mark.django_db
def test_ambiguous_phrases_without_a_state_stay_unresolved(extraction_places):
    assert resolve_place_phrase("Springfield") is None


@pytest.mark.django_db
def test_common_word_names_are_declared():
    assert {"mobile", "independence", "bell", "washington"} <= COMMON_WORD_NAMES


def test_every_body_pattern_declares_a_method():
    assert BODY_PATTERNS
    for pattern, required_place_type, method in BODY_PATTERNS:
        assert pattern.groups == 1
        assert required_place_type
        assert method == "body_pattern"


def test_extracted_place_is_frozen():
    assert ExtractedPlace.__dataclass_params__.frozen  # type: ignore[attr-defined]
