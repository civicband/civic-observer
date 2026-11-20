from datetime import timedelta

import pytest
from django.utils import timezone

from tests.factories import (
    MentionFactory,
    PlaceFactory,
    ThreadFactory,
    ThreadPlaceFactory,
)
from watch.lookup import build_query, capitalized_phrases
from watch.models import PlaceAlias


def test_capitalized_phrases_finds_named_entities():
    assert capitalized_phrases("Beverly Hills Planning Commission meets Tuesday.") == [
        "Beverly Hills Planning Commission",
        "Beverly Hills Planning",
        "Beverly Hills",
        "Beverly",
        "Hills Planning Commission",
        "Hills Planning",
        "Hills",
        "Planning Commission",
        "Planning",
        "Commission",
        "Tuesday",
    ]


def test_capitalized_phrases_never_exceed_four_words():
    phrases = capitalized_phrases("Alpha Bravo Charlie Delta Echo Foxtrot")
    assert phrases
    assert all(len(phrase.split()) <= 4 for phrase in phrases)


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
    thread = ThreadFactory(title="City council")
    assert build_query(thread) == "City council"


@pytest.mark.django_db
def test_query_falls_back_to_place_name():
    place = PlaceFactory(name="Chico", state="CA")
    thread = ThreadFactory(title="")
    assert build_query(thread, place=place) == "Chico"


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
def test_blank_query_override_falls_through():
    place = PlaceFactory(name="Chico", state="CA")
    thread = ThreadFactory(title="Chico budget")
    ThreadPlaceFactory(thread=thread, place=place, query_override="   ")
    assert build_query(thread, place=place) == "budget"


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


@pytest.mark.django_db
def test_title_terms_lead_and_dedupe_case_insensitively():
    thread = ThreadFactory(title="Chico budget CHICO")
    MentionFactory(thread=thread, snippet_text="CHICO Zoning Board")
    query = build_query(thread)
    assert query == "chico budget zoning"
    assert query.split()[0] == "chico"
    assert query.count("chico") == 1


@pytest.mark.django_db
def test_snippet_phrase_adds_term_not_in_title():
    thread = ThreadFactory(title="Budget vote delayed")
    MentionFactory(thread=thread, snippet_text="The Zoning Board will hear it.")
    query = build_query(thread)
    assert "zoning" in query
    assert query == "budget vote delayed zoning"


@pytest.mark.django_db
def test_query_excludes_non_stopword_keyword():
    thread = ThreadFactory(title="Ferry terminal budget")
    MentionFactory(thread=thread, keyword="Ferry terminal")
    assert build_query(thread) == "budget"


@pytest.mark.django_db
def test_query_excludes_place_alias_with_punctuation():
    place = PlaceFactory(name="Marysville", state="CA")
    PlaceAlias.objects.create(
        place=place,
        key="St. Yuba",
        kind=PlaceAlias.Kind.NAME,
        source=PlaceAlias.Source.MANUAL,
    )
    thread = ThreadFactory(title="St. Yuba budget")
    assert build_query(thread, place=place) == "budget"


@pytest.mark.django_db
def test_query_removes_short_tokens():
    thread = ThreadFactory(title="AB budget")
    assert build_query(thread) == "budget"


@pytest.mark.django_db
def test_mentions_are_ordered_by_published_at():
    thread = ThreadFactory(title="Budget")
    MentionFactory(
        thread=thread,
        id="m-old",
        published_at=timezone.now() - timedelta(days=1),
        snippet_text="Harbor",
    )
    MentionFactory(
        thread=thread,
        id="m-new",
        published_at=timezone.now(),
        snippet_text="Zoning",
    )
    assert build_query(thread) == "budget harbor zoning"


@pytest.mark.django_db
def test_mentions_with_equal_timestamps_are_ordered_by_pk():
    thread = ThreadFactory(title="Budget")
    published_at = timezone.now()
    MentionFactory(
        thread=thread,
        id="m-b",
        published_at=published_at,
        snippet_text="Harbor",
    )
    MentionFactory(
        thread=thread,
        id="m-a",
        published_at=published_at,
        snippet_text="Zoning",
    )
    assert build_query(thread) == "budget zoning harbor"
