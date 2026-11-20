import pytest

from tests.factories import (
    MentionFactory,
    PlaceFactory,
    ThreadFactory,
    ThreadPlaceFactory,
)
from watch.lookup import build_query, capitalized_phrases


def test_capitalized_phrases_finds_named_entities():
    assert capitalized_phrases(
        "The Beverly Hills Planning Commission meets Tuesday."
    ) == [
        "The Beverly Hills Planning Commission",
        "Beverly Hills Planning Commission",
        "Planning Commission",
        "Tuesday",
    ]


@pytest.mark.django_db
def test_query_excludes_keyword_and_place():
    place = PlaceFactory(name="Chico", state="CA")
    thread = ThreadFactory(title="Chico council delays budget vote")
    MentionFactory(
        thread=thread,
        keyword="city council",
        snippet_text="The Chico City Council delayed the budget vote.",
    )
    query = build_query(thread, place=place)
    assert "Chico" not in query
    assert "city council" not in query
    assert "budget" in query


@pytest.mark.django_db
def test_query_falls_back_to_title():
    thread = ThreadFactory(title="Only a title")
    assert build_query(thread) == "Only a title"


@pytest.mark.django_db
def test_query_override_is_returned_unchanged():
    place = PlaceFactory(name="Chico", state="CA")
    thread = ThreadFactory(title="Chico council delays budget vote")
    ThreadPlaceFactory(
        thread=thread,
        place=place,
        query_override="custom budget query",
    )
    assert build_query(thread, place=place) == "custom budget query"


@pytest.mark.django_db
def test_query_deduplicates_and_caps_terms():
    thread = ThreadFactory(title="Alpha Bravo")
    MentionFactory(
        thread=thread,
        snippet_text=(
            "Alpha Bravo Charlie Delta Echo Foxtrot Golf Hotel India Juliett "
            "Kilo Lima Mike November Oscar Papa Quebec Romeo Sierra Tango "
            "Uniform Victor Whiskey Xray Yankee Zulu"
        ),
    )
    query = build_query(thread)
    terms = query.split()
    assert len(terms) == 25
    assert len({term.lower() for term in terms}) == len(terms)
