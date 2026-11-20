import json
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from django.core.management import CommandError, call_command
from django.utils import timezone

from watch.models import Place, PlaceAlias, Subreddit
from watch.wikidata import (
    SPARQL_QUERY,
    USER_AGENT,
    WIKIDATA_ENDPOINT,
    load_wikidata,
    parse_wikidata_bindings,
)

FIXTURES = Path(__file__).parent / "fixtures"
BINDINGS_FILE = FIXTURES / "wikidata_bindings.json"


def make_place(canonical_key, name, place_type, state="", *, country="US"):
    return Place.objects.create(
        canonical_key=canonical_key,
        name=name,
        legal_name=name,
        place_type=place_type,
        state=state,
        country=country,
    )


@pytest.fixture
def wikidata_places(db):
    """Places covering exact-name, alias, state, and ambiguity matching."""
    places = {
        "durham_nc": make_place(
            "us:place:3719000", "Durham", Place.PlaceType.CITY, "NC"
        ),
        "durham_ca": make_place(
            "us:place:0620000", "Durham", Place.PlaceType.CITY, "CA"
        ),
        "chapel_hill": make_place(
            "us:place:3711800", "Chapel Hill", Place.PlaceType.CITY, "NC"
        ),
        # The stripped county alias "durham" must never shadow the city.
        "durham_county": make_place(
            "us:county:37063", "Durham County", Place.PlaceType.COUNTY, "NC"
        ),
    }
    PlaceAlias.objects.create(
        place=places["chapel_hill"],
        key="chapelboro",
        kind=PlaceAlias.Kind.NICKNAME,
        source=PlaceAlias.Source.MANUAL,
    )
    PlaceAlias.objects.create(
        place=places["durham_county"],
        key="durham",
        kind=PlaceAlias.Kind.NAME,
        source=PlaceAlias.Source.GAZETTEER,
    )
    return places


def payload(*bindings):
    return {"results": {"bindings": list(bindings)}}


def binding(subreddit, place_name, state=None, country=None):
    row = {
        "subreddit": {"value": subreddit},
        "placeLabel": {"value": place_name},
    }
    if state is not None:
        row["stateLabel"] = {"value": state}
    if country is not None:
        row["countryLabel"] = {"value": country}
    return row


@pytest.mark.django_db
def test_wikidata_links_subreddit_to_place(wikidata_places):
    stats = load_wikidata(
        fetch=lambda endpoint: payload(binding("bullcity", "Durham", "North Carolina"))
    )

    assert stats["linked"] == 1
    row = Subreddit.objects.get(name="bullcity")
    place = row.place
    assert place is not None
    assert place.name == "Durham"
    assert row.source == "wikidata"


@pytest.mark.django_db
def test_wikidata_does_not_overwrite_manual(wikidata_places):
    Subreddit.objects.create(
        name="bullcity", kind="general", source="manual", confidence="high"
    )

    stats = load_wikidata(fetch=lambda endpoint: payload(binding("bullcity", "Durham")))

    assert stats["skipped"] == 1
    row = Subreddit.objects.get(name="bullcity")
    assert row.kind == "general"
    assert row.place is None


@pytest.mark.django_db
def test_loader_sets_wikidata_metadata(wikidata_places):
    load_wikidata(
        fetch=lambda endpoint: payload(binding("bullcity", "Durham", "North Carolina"))
    )

    row = Subreddit.objects.get(name="bullcity")
    assert row.kind == Subreddit.Kind.PLACE
    assert row.confidence == Subreddit.Confidence.HIGH
    assert row.source == Subreddit.Source.WIKIDATA
    assert row.reviewed_at is None


@pytest.mark.django_db
def test_state_label_disambiguates(wikidata_places):
    stats = load_wikidata(
        fetch=lambda endpoint: payload(binding("durham", "Durham", "California"))
    )

    assert stats["linked"] == 1
    assert Subreddit.objects.get(name="durham").place == wikidata_places["durham_ca"]


@pytest.mark.django_db
def test_state_abbreviation_is_accepted(wikidata_places):
    stats = load_wikidata(
        fetch=lambda endpoint: payload(binding("bullcity", "Durham", "NC"))
    )

    assert stats["linked"] == 1
    assert Subreddit.objects.get(name="bullcity").place == wikidata_places["durham_nc"]


@pytest.mark.django_db
def test_ambiguous_name_without_state_is_not_linked(wikidata_places):
    stats = load_wikidata(fetch=lambda endpoint: payload(binding("durham", "Durham")))

    assert stats == {"linked": 0, "unmatched": 1, "skipped": 0}
    assert not Subreddit.objects.exists()


@pytest.mark.django_db
def test_all_bindings_resolving_to_same_place_are_linked(wikidata_places):
    stats = load_wikidata(
        fetch=lambda endpoint: payload(
            binding("bullcity", "Durham", "North Carolina"),
            binding("bullcity", "Durham", "North Carolina"),
        )
    )

    assert stats["linked"] == 1
    assert Subreddit.objects.get(name="bullcity").place == wikidata_places["durham_nc"]


@pytest.mark.django_db
def test_one_unresolved_binding_blocks_the_whole_subreddit(wikidata_places):
    stats = load_wikidata(
        fetch=lambda endpoint: payload(
            binding("bullcity", "Durham", "North Carolina"),
            binding("bullcity", "Atlantis", "North Carolina"),
        )
    )

    assert stats == {"linked": 0, "unmatched": 1, "skipped": 0}
    assert not Subreddit.objects.exists()


@pytest.mark.django_db
def test_conflicting_bindings_are_unmatched(wikidata_places):
    stats = load_wikidata(
        fetch=lambda endpoint: payload(
            binding("triangle", "Durham", "North Carolina"),
            binding("triangle", "Chapel Hill", "North Carolina"),
        )
    )

    assert stats == {"linked": 0, "unmatched": 1, "skipped": 0}
    assert not Subreddit.objects.exists()


@pytest.mark.django_db
def test_unmapped_state_label_is_ignored(wikidata_places):
    stats = load_wikidata(
        fetch=lambda endpoint: payload(
            binding("chapelhill", "Chapel Hill", "Somewhere Else")
        )
    )

    assert stats["linked"] == 1
    assert (
        Subreddit.objects.get(name="chapelhill").place == wikidata_places["chapel_hill"]
    )


@pytest.mark.django_db
def test_country_label_filters_candidates(wikidata_places):
    stats = load_wikidata(
        fetch=lambda endpoint: payload(
            binding("bullcity", "Durham", "North Carolina", "Canada")
        )
    )

    assert stats == {"linked": 0, "unmatched": 1, "skipped": 0}
    assert not Subreddit.objects.exists()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "country", ["England", "Scotland", "Wales", "Republic of Ireland"]
)
def test_unmapped_non_empty_country_is_a_hard_non_match(wikidata_places, country):
    stats = load_wikidata(
        fetch=lambda endpoint: payload(
            binding("bullcity", "Durham", "North Carolina", country)
        )
    )

    assert stats == {"linked": 0, "unmatched": 1, "skipped": 0}
    assert not Subreddit.objects.exists()


@pytest.mark.django_db
def test_unmatched_name_writes_nothing(wikidata_places):
    stats = load_wikidata(
        fetch=lambda endpoint: payload(binding("nowhere", "Atlantis", "North Carolina"))
    )

    assert stats == {"linked": 0, "unmatched": 1, "skipped": 0}
    assert not Subreddit.objects.exists()


@pytest.mark.django_db
def test_exact_place_name_wins_over_alias(wikidata_places):
    stats = load_wikidata(
        fetch=lambda endpoint: payload(binding("bullcity", "Durham", "North Carolina"))
    )

    assert stats["linked"] == 1
    row = Subreddit.objects.get(name="bullcity")
    assert row.place == wikidata_places["durham_nc"]
    assert row.place.place_type == Place.PlaceType.CITY


@pytest.mark.django_db
def test_alias_is_used_when_no_place_name_matches(wikidata_places):
    stats = load_wikidata(
        fetch=lambda endpoint: payload(binding("chapelboro", "Chapelboro"))
    )

    assert stats["linked"] == 1
    assert (
        Subreddit.objects.get(name="chapelboro").place == wikidata_places["chapel_hill"]
    )


@pytest.mark.django_db
def test_alias_is_retried_after_name_candidates_filter_out(wikidata_places):
    make_place("us:place:0612345", "Bull City", Place.PlaceType.CITY, "CA")
    triangle = make_place("us:place:3787654", "Triangle", Place.PlaceType.CITY, "NC")
    PlaceAlias.objects.create(
        place=triangle,
        key="bullcity",
        kind=PlaceAlias.Kind.NICKNAME,
        source=PlaceAlias.Source.MANUAL,
    )

    stats = load_wikidata(
        fetch=lambda endpoint: payload(
            binding("bullcity", "Bull City", "North Carolina")
        )
    )

    assert stats["linked"] == 1
    assert Subreddit.objects.get(name="bullcity").place == triangle


@pytest.mark.django_db
def test_subreddit_prefix_is_stripped(wikidata_places):
    load_wikidata(
        fetch=lambda endpoint: payload(
            binding("r/BullCity", "Durham", "North Carolina")
        )
    )

    row = Subreddit.objects.get(name="bullcity")
    assert row.place == wikidata_places["durham_nc"]


@pytest.mark.django_db
def test_existing_non_manual_row_is_updated(wikidata_places):
    Subreddit.objects.create(name="bullcity", kind="general")

    stats = load_wikidata(
        fetch=lambda endpoint: payload(binding("bullcity", "Durham", "North Carolina"))
    )

    assert stats["linked"] == 1
    row = Subreddit.objects.get(name="bullcity")
    assert row.kind == Subreddit.Kind.PLACE
    assert row.place == wikidata_places["durham_nc"]


@pytest.mark.django_db
def test_manual_row_is_found_case_insensitively(wikidata_places):
    Subreddit.objects.create(
        name="BullCity", kind="general", source="manual", confidence="high"
    )

    stats = load_wikidata(
        fetch=lambda endpoint: payload(binding("bullcity", "Durham", "North Carolina"))
    )

    assert stats["skipped"] == 1
    assert Subreddit.objects.count() == 1
    row = Subreddit.objects.get(name="BullCity")
    assert row.kind == "general"
    assert row.place is None


@pytest.mark.django_db
def test_existing_row_is_updated_case_insensitively(wikidata_places):
    Subreddit.objects.create(name="BullCity", kind="general")

    stats = load_wikidata(
        fetch=lambda endpoint: payload(binding("bullcity", "Durham", "North Carolina"))
    )

    assert stats["linked"] == 1
    assert Subreddit.objects.count() == 1
    row = Subreddit.objects.get(name="BullCity")
    assert row.kind == Subreddit.Kind.PLACE
    assert row.place == wikidata_places["durham_nc"]


@pytest.mark.django_db
def test_existing_review_state_is_preserved(wikidata_places):
    reviewed_at = timezone.now()
    Subreddit.objects.create(name="bullcity", kind="general", reviewed_at=reviewed_at)

    stats = load_wikidata(
        fetch=lambda endpoint: payload(binding("bullcity", "Durham", "North Carolina"))
    )

    assert stats["linked"] == 1
    row = Subreddit.objects.get(name="bullcity")
    assert row.place == wikidata_places["durham_nc"]
    assert row.reviewed_at == reviewed_at


def test_parse_wikidata_bindings_reads_fixture():
    rows = parse_wikidata_bindings(json.loads(BINDINGS_FILE.read_text()))

    assert len(rows) == 5
    assert rows[0] == {
        "subreddit": "bullcity",
        "place_name": "Durham",
        "state": "North Carolina",
        "country": "United States of America",
    }
    assert rows[1]["subreddit"] == "chapelhill"
    assert rows[2] == {
        "subreddit": "durham",
        "place_name": "Durham",
        "state": "",
        "country": "",
    }


def test_parse_wikidata_bindings_tolerates_empty_payload():
    assert parse_wikidata_bindings({}) == []
    assert parse_wikidata_bindings({"results": {}}) == []


@pytest.mark.django_db
def test_command_loads_saved_response(wikidata_places):
    call_command("load_watch_wikidata", "--file", str(BINDINGS_FILE))

    assert Subreddit.objects.count() == 3
    place = Subreddit.objects.get(name="bullcity").place
    assert place is not None
    assert place.name == "Durham"
    assert not Subreddit.objects.filter(name="durham").exists()
    assert not Subreddit.objects.filter(name="nowhere").exists()


@pytest.mark.django_db
def test_command_dry_run_writes_nothing(wikidata_places):
    out = StringIO()

    call_command(
        "load_watch_wikidata",
        "--file",
        str(BINDINGS_FILE),
        "--dry-run",
        stdout=out,
    )

    assert not Subreddit.objects.exists()
    assert "linked=3" in out.getvalue()
    assert "unmatched=2" in out.getvalue()


@pytest.mark.django_db
def test_command_skips_manual_rows(wikidata_places):
    Subreddit.objects.create(
        name="manualplace", kind="general", source="manual", confidence="high"
    )

    call_command("load_watch_wikidata", "--file", str(BINDINGS_FILE))

    assert Subreddit.objects.get(name="manualplace").kind == "general"
    assert Subreddit.objects.count() == 3


def test_command_rejects_missing_file():
    with pytest.raises(CommandError, match="Wikidata response file not found"):
        call_command("load_watch_wikidata", "--file", "/nonexistent/bindings.json")


@pytest.mark.parametrize(
    "contents",
    [
        "[]",
        "{}",
        '{"results": {}}',
        '{"results": {"bindings": {}}}',
    ],
)
def test_command_rejects_payload_without_bindings_list(tmp_path, contents):
    path = tmp_path / "bad_bindings.json"
    path.write_text(contents, encoding="utf-8")

    with pytest.raises(CommandError, match="results.bindings"):
        call_command("load_watch_wikidata", "--file", str(path))


def test_default_fetch_uses_default_endpoint():
    response = MagicMock()
    response.json.return_value = payload()

    with patch("watch.wikidata.httpx.get", return_value=response) as get:
        stats = load_wikidata()

    get.assert_called_once_with(
        WIKIDATA_ENDPOINT,
        params={"query": SPARQL_QUERY, "format": "json"},
        headers={"User-Agent": USER_AGENT},
        timeout=60,
    )
    response.raise_for_status.assert_called_once_with()
    assert stats == {"linked": 0, "unmatched": 0, "skipped": 0}


@pytest.mark.django_db
def test_command_endpoint_is_forwarded_to_default_fetch(wikidata_places):
    response = MagicMock()
    response.json.return_value = payload()

    with patch("watch.wikidata.httpx.get", return_value=response) as get:
        call_command("load_watch_wikidata", "--endpoint", "https://example.test/sparql")

    assert get.call_args.args == ("https://example.test/sparql",)
    assert get.call_args.kwargs["params"]["query"] == SPARQL_QUERY
    assert get.call_args.kwargs["params"]["format"] == "json"
    assert get.call_args.kwargs["headers"] == {"User-Agent": USER_AGENT}
    assert get.call_args.kwargs["timeout"] == 60
