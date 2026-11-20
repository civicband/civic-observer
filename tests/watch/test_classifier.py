import csv
from datetime import timedelta
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import CommandError, call_command
from django.utils import timezone

from tests.watch.fixtures.classifier_places import (
    create_classifier_labels,
    create_classifier_places,
)
from watch.classify import (
    REGIONS,
    Classification,
    _country_hint,
    apply_classification,
    classify_pending_subreddits,
    classify_subreddit,
)
from watch.gazetteer import refresh_aliases
from watch.models import Place, Subreddit

DATA = Path(__file__).resolve().parents[2] / "watch" / "data"
SUBREDDITS_CSV = DATA / "subreddits.csv"


@pytest.fixture
def classifier_places(db):
    return create_classifier_places()


@pytest.fixture
def classifier_labels(classifier_places):
    return create_classifier_labels(classifier_places)


def read_subreddits_csv(path):
    with path.open(newline="") as fh:
        return list(csv.DictReader(fh))


@pytest.mark.django_db
def test_classifier_reproduces_every_hand_label(classifier_places, classifier_labels):
    rows = read_subreddits_csv(SUBREDDITS_CSV)
    names = {row["subreddit"] for row in rows}

    assert len(rows) == 63
    assert names == set(classifier_labels)

    for row in rows:
        name = row["subreddit"]
        expected = classifier_labels[name]
        result = classify_subreddit(name, use_manual=False)

        if expected["kind"] == "general":
            assert result.kind == "general", (name, result)
            assert result.place is None, (name, result)

        elif expected["kind"] == "state":
            assert result.kind == "state", (name, result)
            assert result.state == expected["state"], (name, result)

        elif expected["kind"] == "region":
            assert result.kind == "region", (name, result)
            assert result.state == expected["state"], (name, result)

        elif expected["kind"] == "non_us":
            # A non-US hand label may fall through to general/ambiguous or a
            # Canadian place, but it must never land on a US place.
            assert result.kind in {"non_us", "general", "ambiguous"} or (
                result.kind == "place"
                and result.place is not None
                and str(result.place.country) == "CA"
            ), (name, result)
            if result.place is not None:
                assert str(result.place.country) == "CA", (name, result)
            elif result.kind == "non_us":
                assert result.country == expected["country"], (name, result)

        elif expected["kind"] == "ambiguous":
            # ``glendale``/``portland`` are truly ambiguous; a few manual-only
            # rows simply have no generated alias and fall through.
            assert result.place is None, (name, result)
            if name not in {"sfv", "westseattlewa", "bullcity"}:
                assert result.kind == "ambiguous", (name, result)

        else:
            assert result.kind == "place", (name, result)
            assert result.place == expected["place"], (name, result)


@pytest.mark.django_db
def test_classifier_matches_unambiguous_hand_labels(classifier_places):
    expected = {
        "sandiego": "San Diego",
        "chicoca": "Chico",
        "losangeles": "Los Angeles",
        "chestercounty": "Chester County",
        "ithaca": "Ithaca",
        "buffalo": "Buffalo",
        "santaana": "Santa Ana",
        "palmsprings": "Palm Springs",
        "butte": "Butte",
        "lacrossewi": "La Crosse",
        "seattle": "Seattle",
        "ranchocordova": "Rancho Cordova",
        "mckinney": "McKinney",
        "fountaincolorado": "Fountain",
        "littleton": "Littleton",
        "eugene": "Eugene",
        "sanmateo": "San Mateo",
        "iowacity": "Iowa City",
        "eastpaloalto": "East Palo Alto",
        "cecilcounty": "Cecil County",
    }
    for subreddit, place_name in expected.items():
        result = classify_subreddit(subreddit, use_manual=False)
        assert result.kind == "place", subreddit
        assert result.place is not None, subreddit
        assert result.place.name == place_name, subreddit


@pytest.mark.django_db
def test_development_slc_uses_alias(classifier_places):
    result = classify_subreddit("developmentslc", use_manual=False)
    assert result.place is not None
    assert result.place.name == "Salt Lake City"
    assert result.confidence == "low"


@pytest.mark.django_db
def test_glendale_is_ambiguous(classifier_places):
    result = classify_subreddit("glendale", use_manual=False)
    assert result.kind == "ambiguous"
    assert result.place is None


@pytest.mark.django_db
def test_state_names_beat_same_named_places(classifier_places):
    assert Place.objects.filter(name="Oregon").count() == 3

    oregon = classify_subreddit("oregon", use_manual=False)
    assert oregon.kind == "state"
    assert oregon.state == "OR"
    assert oregon.place is None

    oregon_talk = classify_subreddit("oregontalk", use_manual=False)
    assert oregon_talk.kind == "state"
    assert oregon_talk.state == "OR"
    assert oregon_talk.place is None


@pytest.mark.django_db
def test_bare_state_abbreviations_are_not_state_keys(classifier_places):
    assert classify_subreddit("ca", use_manual=False).kind != "state"
    assert classify_subreddit("or", use_manual=False).kind != "state"


@pytest.mark.django_db
def test_brisbane_resolves_to_australia_not_california(classifier_places):
    assert Place.objects.filter(name="Brisbane", country="US").exists()

    result = classify_subreddit("brisbane", use_manual=False)
    assert result.kind == "non_us"
    assert result.place is None
    assert result.country == "AU"


@pytest.mark.django_db
def test_galway_resolves_to_ireland_not_new_york(classifier_places):
    assert Place.objects.filter(name="Galway", country="US").exists()

    result = classify_subreddit("galway", use_manual=False)
    assert result.kind == "non_us"
    assert result.place is None
    assert result.country == "IE"


@pytest.mark.django_db
def test_portland_or_and_me_are_ambiguous(classifier_places):
    result = classify_subreddit("portland", use_manual=False)
    assert result.kind == "ambiguous"
    assert result.place is None


@pytest.mark.django_db
def test_canadian_cities_do_not_preempt_us_namesakes(classifier_places):
    canadian = {
        "richmond": ("Richmond", "BC"),
        "vancouver": ("Vancouver", "BC"),
        "victoria": ("Victoria", "BC"),
        "windsor": ("Windsor", "ON"),
    }
    for subreddit, (name, province) in canadian.items():
        assert Place.objects.filter(name=name, state=province, country="CA").exists(), (
            subreddit
        )
        assert Place.objects.filter(name=name, country="US").exists(), subreddit

        result = classify_subreddit(subreddit, use_manual=False)
        assert result.kind == "ambiguous", (subreddit, result)
        assert result.place is None, (subreddit, result)


@pytest.mark.django_db
def test_unopposed_canadian_alias_still_resolves_to_place(classifier_places):
    result = classify_subreddit("victoriabc", use_manual=False)
    assert result.kind == "place"
    assert result.place is not None
    assert result.place.name == "Victoria"
    assert result.country == "CA"


@pytest.mark.django_db
@pytest.mark.parametrize("subreddit", ["victoriassecret", "readinglist", "dorchester"])
def test_non_us_matching_never_uses_substrings(classifier_places, subreddit):
    result = classify_subreddit(subreddit, use_manual=False)
    assert result.kind in {"general", "ambiguous"}, (subreddit, result)
    assert result.place is None, (subreddit, result)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("subreddit", "place_name"),
    [
        ("losangeles", "Los Angeles"),
        ("sandiego", "San Diego"),
        ("lacrossewi", "La Crosse"),
        ("sanmateo", "San Mateo"),
        ("alameda", "Alameda"),
        ("monterey", "Monterey"),
        ("sonoma", "Sonoma"),
        ("orange", "Orange"),
    ],
)
def test_settlement_wins_over_same_named_county(
    classifier_places, subreddit, place_name
):
    assert Place.objects.filter(
        name=f"{place_name} County", place_type=Place.PlaceType.COUNTY
    ).exists()

    result = classify_subreddit(subreddit, use_manual=False)

    assert result.kind == "place", (subreddit, result)
    assert result.place is not None, (subreddit, result)
    assert result.place.name == place_name, (subreddit, result)
    assert result.place.place_type != Place.PlaceType.COUNTY


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("subreddit", "place_name"),
    [
        ("cecilcounty", "Cecil County"),
        ("alamedacounty", "Alameda County"),
        ("orangecounty", "Orange County"),
    ],
)
def test_county_only_names_resolve_to_the_county(
    classifier_places, subreddit, place_name
):
    result = classify_subreddit(subreddit, use_manual=False)

    assert result.kind == "place", (subreddit, result)
    assert result.place is not None, (subreddit, result)
    assert result.place.place_type == Place.PlaceType.COUNTY
    assert result.place.name == place_name


@pytest.mark.django_db
def test_county_population_does_not_shadow_the_city(classifier_places):
    city = Place.objects.get(name="Chester", place_type=Place.PlaceType.CITY)
    county = Place.objects.get(canonical_key="us:county:42029")
    assert (county.population or 0) > (city.population or 0)

    result = classify_subreddit("chester", use_manual=False)

    assert result.place == city


@pytest.mark.django_db
def test_manual_override_wins(classifier_places):
    Subreddit.objects.create(
        name="chicoca", kind="general", source="manual", confidence="high"
    )
    result = classify_subreddit("chicoca", use_manual=True)
    assert result.kind == "general"


@pytest.mark.django_db
def test_manual_row_is_ignored_without_use_manual(classifier_places):
    Subreddit.objects.create(
        name="chicoca", kind="general", source="manual", confidence="high"
    )
    result = classify_subreddit("chicoca", use_manual=False)
    assert result.kind == "place"
    assert result.place is not None
    assert result.place.name == "Chico"


@pytest.mark.django_db
def test_wikidata_row_wins_over_gazetteer(classifier_places):
    Subreddit.objects.create(
        name="chicoca",
        kind="place",
        place=classifier_places["chicoca"],
        state="CA",
        country="US",
        confidence="medium",
        source="wikidata",
        note="from wikidata",
    )
    result = classify_subreddit("chicoca", use_manual=False)
    assert result.kind == "place"
    assert result.source == "wikidata"
    assert result.confidence == "medium"
    assert result.note == "from wikidata"


@pytest.mark.django_db
def test_ambiguity_requires_ten_times_the_next_place(classifier_places):
    populated = Place.objects.create(
        canonical_key="us:place:test-metropolis-il",
        name="Metropolis",
        place_type=Place.PlaceType.CITY,
        state="IL",
        country="US",
        population=100000,
    )
    rival = Place.objects.create(
        canonical_key="us:place:test-metropolis-xx",
        name="Metropolis",
        place_type=Place.PlaceType.CITY,
        state="XX",
        country="US",
        population=5000,
    )
    refresh_aliases(populated)
    refresh_aliases(rival)

    assert classify_subreddit("metropolis", use_manual=False).place == populated

    Place.objects.filter(pk=rival.pk).update(population=20000)
    assert classify_subreddit("metropolis", use_manual=False).kind == "ambiguous"


@pytest.mark.django_db
def test_state_and_region_layers(classifier_places):
    south_carolina = classify_subreddit("southcarolina", use_manual=False)
    assert south_carolina.kind == "state"
    assert south_carolina.state == "SC"
    assert south_carolina.place is None

    oregon = classify_subreddit("oregontalk", use_manual=False)
    assert oregon.kind == "state"
    assert oregon.state == "OR"
    assert oregon.confidence == "low"

    bay_area = classify_subreddit("bayarea", use_manual=False)
    assert bay_area.kind == "region"
    assert bay_area.state == "CA"
    assert bay_area.note == "Bay Area"

    pnw_daily = classify_subreddit("pnwdaily", use_manual=False)
    assert pnw_daily.kind == "region"
    assert pnw_daily.state == ""
    assert pnw_daily.note == "Pacific Northwest"
    assert pnw_daily.confidence == "high"

    assert REGIONS["dmv"] == (None, "DC/Maryland/Virginia")
    dmv = classify_subreddit("dmv", use_manual=False)
    assert dmv.kind == "region"
    assert dmv.state == ""


@pytest.mark.django_db
def test_non_us_city_layer(classifier_places):
    result = classify_subreddit("london", use_manual=False)
    assert result.kind == "non_us"
    assert result.place is None
    assert result.country == "GB"


@pytest.mark.django_db
def test_affix_stripping_reaches_non_us_city_layer(classifier_places):
    result = classify_subreddit("londonnews", use_manual=False)
    assert result.kind == "non_us"
    assert result.country == "GB"
    assert result.confidence == "low"


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("key", "country"),
    [
        ("northernireland", "GB"),
        ("northernirelandnews", "GB"),
        ("housingireland", "IE"),
        ("irelandtransport", "IE"),
        ("theunitedkingdomnews", "GB"),
    ],
)
def test_country_hints_match_whole_tokens(classifier_places, key, country):
    assert _country_hint(key) == country


@pytest.mark.django_db
def test_northern_ireland_is_not_ireland(classifier_places):
    result = classify_subreddit("northernirelandnews", use_manual=False)

    assert result.country != "IE", result
    assert result.kind in {"non_us", "general", "ambiguous"}, result


@pytest.mark.django_db
def test_blank_name_is_general(classifier_places):
    result = classify_subreddit("   ", use_manual=False)
    assert result.kind == "general"
    assert result.place is None


@pytest.mark.django_db
def test_canadian_non_us_alias_falls_back_to_place(classifier_places):
    result = classify_subreddit("vancity", use_manual=False)
    assert result.kind == "place"
    assert result.place is not None
    assert result.place.name == "Vancouver"
    assert result.country == "CA"


@pytest.mark.django_db
def test_general_fallback(classifier_places):
    result = classify_subreddit("lol", use_manual=False)
    assert result.kind == "general"
    assert result.place is None
    assert result.confidence == "low"
    assert result.source == "gazetteer"


@pytest.mark.django_db
def test_apply_classification_preserves_mute_and_existing_note(classifier_places):
    subreddit = Subreddit.objects.create(
        name="chicoca",
        kind="general",
        note="operator note",
        muted=True,
        source="",
    )
    result = classify_subreddit("chicoca", use_manual=False)

    assert apply_classification(subreddit, result) is True
    subreddit.refresh_from_db()

    assert subreddit.kind == "place"
    assert subreddit.place == classifier_places["chicoca"]
    assert subreddit.state == "CA"
    assert subreddit.country == "US"
    assert subreddit.confidence == result.confidence
    assert subreddit.source == "gazetteer"
    # Classification is not review: the operator still owns reviewed_at.
    assert subreddit.reviewed_at is None
    assert subreddit.classified_at is not None
    assert subreddit.muted is True
    assert subreddit.note == "operator note"

    assert apply_classification(subreddit, result) is False


@pytest.mark.django_db
def test_apply_classification_keeps_operator_review_timestamp(classifier_places):
    reviewed_at = timezone.now() - timedelta(days=3)
    subreddit = Subreddit.objects.create(
        name="chicoca",
        kind="general",
        source="gazetteer",
        reviewed_at=reviewed_at,
    )
    result = classify_subreddit("chicoca", use_manual=False)

    assert apply_classification(subreddit, result) is True
    subreddit.refresh_from_db()
    assert subreddit.reviewed_at == reviewed_at
    assert subreddit.classified_at is not None


@pytest.mark.django_db
def test_apply_classification_updates_modified(classifier_places):
    subreddit = Subreddit.objects.create(
        name="chicoca", kind="general", source="gazetteer"
    )
    old = timezone.now() - timedelta(days=1)
    Subreddit.objects.filter(pk=subreddit.pk).update(modified=old)
    subreddit.refresh_from_db()
    result = classify_subreddit("chicoca", use_manual=False)

    assert apply_classification(subreddit, result) is True

    subreddit.refresh_from_db()
    assert subreddit.modified > old


@pytest.mark.django_db
def test_apply_classification_fills_blank_note():
    subreddit = Subreddit.objects.create(name="bayarea", kind="general", note="")
    result = Classification(
        kind="region",
        place=None,
        state="CA",
        country=None,
        confidence="medium",
        source="gazetteer",
        note="Bay Area",
    )

    assert apply_classification(subreddit, result) is True
    subreddit.refresh_from_db()
    assert subreddit.note == "Bay Area"
    assert subreddit.muted is False


@pytest.mark.django_db
def test_apply_classification_clears_stale_place(classifier_places):
    subreddit = Subreddit.objects.create(
        name="glendale",
        kind="place",
        place=classifier_places["chicoca"],
        state="CA",
        source="gazetteer",
        confidence="high",
    )
    result = classify_subreddit("glendale", use_manual=False)

    assert apply_classification(subreddit, result) is True
    subreddit.refresh_from_db()
    assert subreddit.kind == "ambiguous"
    assert subreddit.place is None
    assert subreddit.state == ""


@pytest.mark.django_db
def test_classify_pending_selects_unclassified_non_manual_rows(classifier_places):
    pending = Subreddit.objects.create(name="chicoca", kind="general", source="")
    # reviewed_at is an operator timestamp; it must not hide a row that has no
    # classification suggestion yet.
    reviewed_but_unclassified = Subreddit.objects.create(
        name="seattle",
        kind="general",
        source="gazetteer",
        reviewed_at=timezone.now(),
    )
    classified = Subreddit.objects.create(
        name="buffalo",
        kind="general",
        source="gazetteer",
        classified_at=timezone.now(),
    )
    manual = Subreddit.objects.create(
        name="sandiego",
        kind="general",
        source="manual",
        reviewed_at=timezone.now(),
    )
    unclassified_manual = Subreddit.objects.create(
        name="eugene",
        kind="general",
        source="manual",
    )

    stats = classify_pending_subreddits()

    assert stats == {"checked": 2, "changed": 2, "unchanged": 0}
    pending.refresh_from_db()
    reviewed_but_unclassified.refresh_from_db()
    classified.refresh_from_db()
    manual.refresh_from_db()
    unclassified_manual.refresh_from_db()
    assert pending.place == classifier_places["chicoca"]
    assert reviewed_but_unclassified.place == classifier_places["seattle"]
    assert reviewed_but_unclassified.reviewed_at is not None
    assert classified.place is None
    assert manual.place is None
    assert unclassified_manual.place is None
    assert unclassified_manual.reviewed_at is None


@pytest.mark.django_db
def test_classify_pending_reclassify_reruns_classified_non_manual_rows(
    classifier_places,
):
    Subreddit.objects.create(
        name="chicoca",
        kind="general",
        source="gazetteer",
        reviewed_at=timezone.now(),
        classified_at=timezone.now(),
    )
    Subreddit.objects.create(
        name="seattle",
        kind="general",
        source="gazetteer",
        reviewed_at=timezone.now(),
        classified_at=timezone.now(),
    )
    Subreddit.objects.create(
        name="sandiego",
        kind="general",
        source="manual",
        reviewed_at=timezone.now(),
        classified_at=timezone.now(),
    )

    stats = classify_pending_subreddits(reclassify=True, limit=2)

    assert stats["checked"] == 2
    assert Subreddit.objects.get(name="chicoca").place is not None
    assert Subreddit.objects.get(name="seattle").place is not None
    assert Subreddit.objects.get(name="sandiego").place is None


@pytest.mark.django_db
def test_classify_pending_skips_already_classified_rows(classifier_places):
    Subreddit.objects.create(
        name="chicoca",
        kind="general",
        source="gazetteer",
        classified_at=timezone.now(),
    )

    stats = classify_pending_subreddits()

    assert stats == {"checked": 0, "changed": 0, "unchanged": 0}
    assert Subreddit.objects.get(name="chicoca").place is None


@pytest.mark.django_db
def test_classify_command_supports_reclassify_and_limit(classifier_places):
    Subreddit.objects.create(name="seattle", kind="general", source="")
    stdout = StringIO()

    call_command(
        "classify_watch_subreddits",
        reclassify=True,
        limit=1,
        stdout=stdout,
    )

    assert "checked=1" in stdout.getvalue()
    assert Subreddit.objects.get(name="seattle").place is not None


@pytest.mark.django_db
def test_classify_command_rejects_non_positive_limit():
    with pytest.raises(CommandError):
        call_command("classify_watch_subreddits", limit=0)
