"""Tests for body-pattern extraction and phrase resolution."""

from __future__ import annotations

import uuid

import pytest

from watch.extraction import (
    BODY_PATTERNS,
    COMMON_WORD_NAMES,
    ExtractedPlace,
    extract_body_patterns,
    resolve_place_phrase,
)
from watch.models import Place, PlaceAlias
from watch.normalize import normalize_key


def _alias_place(
    name: str,
    population: int | None,
    *,
    place_type: str = Place.PlaceType.CITY,
) -> Place:
    """Create a place with one manual name alias for resolver tests."""
    place = Place.objects.create(
        canonical_key=f"us:place:test-{uuid.uuid4().hex}",
        name=name,
        legal_name=name,
        place_type=place_type,
        state="CA",
        population=population,
    )
    PlaceAlias.objects.create(
        place=place,
        key=normalize_key(name),
        kind=PlaceAlias.Kind.NAME,
        source=PlaceAlias.Source.MANUAL,
    )
    return place


@pytest.mark.django_db
def test_city_council_pattern_resolves_subject(extraction_places):
    results = extract_body_patterns(
        "The Beverly Hills Planning Commission meets Tuesday."
    )
    assert [(r.place.name, r.is_subject) for r in results] == [("Beverly Hills", True)]


@pytest.mark.django_db
def test_county_board_resolves_one_county(extraction_places):
    results = extract_body_patterns("Riverside County Board of Supervisors voted yes.")
    assert [(r.place.name, r.place.place_type) for r in results] == [
        ("Riverside County", "county")
    ]


@pytest.mark.django_db
def test_bare_board_rule_also_resolves_one_county(extraction_places):
    results = extract_body_patterns("Riverside Board of Supervisors voted yes.")
    assert [(r.place.name, r.place.place_type) for r in results] == [
        ("Riverside County", "county")
    ]


@pytest.mark.django_db
def test_cdp_rolls_up_to_county(extraction_places):
    results = extract_body_patterns("The North Richmond Planning Commission met.")
    assert [(r.place.name, r.place.place_type) for r in results] == [
        ("Contra Costa County", "county")
    ]


@pytest.mark.django_db
def test_cdp_without_a_county_stays_a_cdp():
    _alias_place("County Less", 4112, place_type=Place.PlaceType.CDP)
    place = resolve_place_phrase("County Less")
    assert place is not None
    assert place.place_type == Place.PlaceType.CDP


@pytest.mark.django_db
def test_common_word_place_requires_capitalization(extraction_places):
    assert extract_body_patterns("we need mobile city council reform") == []
    assert extract_body_patterns("Mobile City Council voted")[0].place.name == "Mobile"


@pytest.mark.django_db
def test_common_word_guard_applies_to_direct_resolution(extraction_places):
    assert resolve_place_phrase("mobile") is None
    place = resolve_place_phrase("Mobile")
    assert place is not None
    assert place.name == "Mobile"


@pytest.mark.django_db
def test_greedy_capture_tries_trailing_subphrases(extraction_places):
    results = extract_body_patterns("In Beverly Hills Planning Commission met.")
    assert [(r.place.name, r.matched_text) for r in results] == [
        ("Beverly Hills", "Beverly Hills")
    ]


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
        ("Riverside County", "county")
    ]


@pytest.mark.django_db
def test_planning_commission_prefers_the_settlement(extraction_places):
    results = extract_body_patterns("San Diego Planning Commission met.")
    assert [(r.place.name, r.place.place_type) for r in results] == [
        ("San Diego", "city")
    ]


@pytest.mark.django_db
def test_state_prior_breaks_a_name_tie(extraction_places):
    place = resolve_place_phrase("Springfield", state="IL")
    assert place is not None
    assert place.state == "IL"


@pytest.mark.django_db
def test_state_prior_falls_back_when_no_candidate_matches(extraction_places):
    place = resolve_place_phrase("Mobile", state="TX")
    assert place is not None
    assert place.name == "Mobile"


@pytest.mark.django_db
def test_ambiguous_phrases_without_a_state_stay_unresolved(extraction_places):
    assert resolve_place_phrase("Springfield") is None


@pytest.mark.django_db
def test_population_winner_needs_a_tenfold_lead():
    winner = _alias_place("Ratio Place", 100000)
    _alias_place("Ratio Place", 10000)
    assert resolve_place_phrase("Ratio Place") == winner


@pytest.mark.parametrize(
    ("top_population", "runner_up_population"),
    [(100000, None), (None, 100000), (100000, 0)],
)
@pytest.mark.django_db
def test_unknown_population_does_not_prove_a_tenfold_lead(
    top_population, runner_up_population
):
    _alias_place("Unknown Ratio", top_population)
    _alias_place("Unknown Ratio", runner_up_population)
    assert resolve_place_phrase("Unknown Ratio") is None


@pytest.mark.django_db
def test_common_word_names_are_declared():
    assert {"mobile", "independence", "bell", "washington"} <= COMMON_WORD_NAMES


def test_every_body_pattern_declares_a_named_place_group():
    assert BODY_PATTERNS
    for pattern, required_place_type, method in BODY_PATTERNS:
        assert pattern.groups == 1
        assert pattern.groupindex == {"place": 1}
        assert required_place_type
        assert method == "body_pattern"


def test_extracted_place_is_frozen():
    assert ExtractedPlace.__dataclass_params__.frozen  # type: ignore[attr-defined]
