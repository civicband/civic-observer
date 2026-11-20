from datetime import timedelta
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command
from django.utils import timezone

from watch.models import Place, PlaceAlias, Subreddit
from watch.subreddits import (
    infer_country,
    resolve_manual_place,
    seed_aliases,
    seed_subreddits,
)

DATA = Path(__file__).resolve().parents[2] / "watch" / "data"
SUBREDDITS_CSV = DATA / "subreddits.csv"
ALIASES_CSV = DATA / "aliases.csv"


def make_place(
    canonical_key,
    name,
    place_type,
    state="",
    *,
    legal_name=None,
    country="US",
):
    return Place.objects.create(
        canonical_key=canonical_key,
        name=name,
        legal_name=legal_name,
        place_type=place_type,
        state=state,
        country=country,
    )


@pytest.fixture
def subreddit_places(db):
    """Create a small gazetteer fixture covering the seeded labels."""
    places = {
        "chico": make_place("us:place:0613010", "Chico", Place.PlaceType.CITY, "CA"),
        "san_diego": make_place(
            "us:place:0666000", "San Diego", Place.PlaceType.CITY, "CA"
        ),
        "los_angeles": make_place(
            "us:place:0644000", "Los Angeles", Place.PlaceType.CITY, "CA"
        ),
        "salt_lake_city": make_place(
            "us:place:4967000", "Salt Lake City", Place.PlaceType.CITY, "UT"
        ),
        "cecil_county": make_place(
            "us:county:24015",
            "Cecil County",
            Place.PlaceType.COUNTY,
            "MD",
            legal_name="Cecil County",
        ),
        # A same-named city must never win the county-only lookup.
        "cecil_city": make_place(
            "us:place:2414300", "Cecil", Place.PlaceType.CITY, "MD"
        ),
        "glendale_ca": make_place(
            "us:place:0630000", "Glendale", Place.PlaceType.CITY, "CA"
        ),
        "glendale_az": make_place(
            "us:place:0427824", "Glendale", Place.PlaceType.CITY, "AZ"
        ),
    }
    return places


@pytest.mark.django_db
def test_seed_subreddits_resolves_places_and_counties(subreddit_places):
    stats = seed_subreddits(SUBREDDITS_CSV)

    assert stats["created"] == 63
    assert stats["updated"] == 0
    assert stats["unchanged"] == 0
    assert stats["unresolved"] > 0

    chicoca = Subreddit.objects.get(name="chicoca")
    assert chicoca.place is not None
    assert chicoca.place.name == "Chico"
    cecil = Subreddit.objects.get(name="cecilcounty")
    assert cecil.place == subreddit_places["cecil_county"]
    assert cecil.place.place_type == "county"
    assert Subreddit.objects.get(name="glendale").place is None
    assert Subreddit.objects.get(name="kingstonontario").kind == "non_us"


@pytest.mark.django_db
def test_seed_subreddits_preserves_labels_and_infers_countries(subreddit_places):
    seed_subreddits(SUBREDDITS_CSV)

    sfv = Subreddit.objects.get(name="sfv")
    assert sfv.place == subreddit_places["los_angeles"]
    assert sfv.note.startswith("San Fernando Valley")
    assert sfv.kind == "place"
    assert sfv.state == "CA"
    assert sfv.confidence == "high"
    assert sfv.source == "manual"
    assert sfv.reviewed_at is not None

    assert Subreddit.objects.get(name="southcarolina").kind == "state"
    assert Subreddit.objects.get(name="southcarolina").state == "SC"
    assert Subreddit.objects.get(name="pnwdaily").kind == "region"
    assert Subreddit.objects.get(name="pnwdaily").note.startswith("Pacific Northwest")

    assert Subreddit.objects.get(name="brum").country.code == "GB"
    assert Subreddit.objects.get(name="kingstonontario").country.code == "CA"
    assert Subreddit.objects.get(name="auckland").country.code == "NZ"
    assert Subreddit.objects.get(name="lovebelfast").name == "lovebelfast"
    assert Subreddit.objects.get(name="lol").kind == "general"


@pytest.mark.django_db
def test_seed_is_idempotent_and_preserves_mute(subreddit_places):
    seed_subreddits(SUBREDDITS_CSV)
    Subreddit.objects.filter(name="lol").update(muted=True, note="memes")

    stats = seed_subreddits(SUBREDDITS_CSV)

    row = Subreddit.objects.get(name="lol")
    assert row.muted is True
    assert row.note == "memes"
    assert stats["created"] == 0
    assert stats["updated"] == 0
    assert stats["unchanged"] == 63


@pytest.mark.django_db
def test_seed_preserves_operator_edits_by_default(subreddit_places):
    seed_subreddits(SUBREDDITS_CSV)
    reviewed_at = timezone.now() - timedelta(days=2)
    Subreddit.objects.filter(name="chicoca").update(
        note="operator note",
        place=subreddit_places["glendale_ca"],
        kind="general",
        reviewed_at=reviewed_at,
    )

    stats = seed_subreddits(SUBREDDITS_CSV)

    row = Subreddit.objects.get(name="chicoca")
    assert row.note == "operator note"
    assert row.place == subreddit_places["glendale_ca"]
    assert row.kind == "general"
    assert row.reviewed_at == reviewed_at
    assert stats["updated"] == 0
    assert stats["unchanged"] == 63


@pytest.mark.django_db
def test_seed_refresh_labels_forces_csv_values(subreddit_places):
    seed_subreddits(SUBREDDITS_CSV)
    old_reviewed_at = timezone.now() - timedelta(days=2)
    Subreddit.objects.filter(name="chicoca").update(
        note="operator note",
        place=subreddit_places["glendale_ca"],
        kind="general",
        reviewed_at=old_reviewed_at,
    )

    stats = seed_subreddits(SUBREDDITS_CSV, refresh_labels=True)

    row = Subreddit.objects.get(name="chicoca")
    assert row.note == ""
    assert row.place == subreddit_places["chico"]
    assert row.kind == "place"
    assert row.reviewed_at is not None
    assert row.reviewed_at > old_reviewed_at
    # A forced refresh re-stamps reviewed_at on every existing row.
    assert stats["updated"] == 63
    assert stats["unchanged"] == 0


@pytest.mark.django_db
def test_seed_refresh_labels_still_preserves_mute(subreddit_places):
    seed_subreddits(SUBREDDITS_CSV)
    Subreddit.objects.filter(name="lol").update(muted=True, note="memes")

    seed_subreddits(SUBREDDITS_CSV, refresh_labels=True)

    row = Subreddit.objects.get(name="lol")
    assert row.muted is True
    assert row.note == ""


@pytest.mark.django_db
def test_seed_keeps_existing_labels_when_csv_note_is_blank(subreddit_places):
    Subreddit.objects.create(name="sandiego", kind="general", note="operator note")

    seed_subreddits(SUBREDDITS_CSV)

    row = Subreddit.objects.get(name="sandiego")
    assert row.note == "operator note"
    assert row.kind == "general"
    assert row.place is None


@pytest.mark.django_db
def test_seed_refresh_fills_an_existing_blank_row(subreddit_places):
    Subreddit.objects.create(name="sandiego", kind="general", note="operator note")

    seed_subreddits(SUBREDDITS_CSV, refresh_labels=True)

    row = Subreddit.objects.get(name="sandiego")
    assert row.kind == "place"
    assert row.place == subreddit_places["san_diego"]


@pytest.mark.django_db
def test_seed_updates_existing_rows_case_insensitively(subreddit_places):
    Subreddit.objects.create(name="ChicoCA", kind="general")

    stats = seed_subreddits(SUBREDDITS_CSV, refresh_labels=True)

    assert Subreddit.objects.count() == 63
    row = Subreddit.objects.get(name="ChicoCA")
    assert row.place == subreddit_places["chico"]
    assert stats["created"] == 62
    assert stats["updated"] == 1


@pytest.mark.django_db
def test_resolve_manual_place_prefers_county_and_requires_unambiguous_name(
    subreddit_places,
):
    assert (
        resolve_manual_place(None, "MD", "Cecil County")
        == subreddit_places["cecil_county"]
    )
    assert resolve_manual_place("Chico", "CA", None) == subreddit_places["chico"]
    assert (
        resolve_manual_place("Glendale", "CA", None) == subreddit_places["glendale_ca"]
    )
    assert resolve_manual_place("Glendale", None, None) is None
    assert resolve_manual_place("Nowhere", "CA", None) is None
    assert resolve_manual_place(None, None, None) is None


@pytest.mark.parametrize(
    ("note", "expected"),
    [
        ("Ireland", "IE"),
        ("UK", "GB"),
        ("Birmingham UK", "GB"),
        ("United Kingdom", "GB"),
        ("Australia", "AU"),
        ("New Zealand", "NZ"),
        ("Canada", "CA"),
        ("Vancouver BC (or Vancouver WA?)", "CA"),
        ("Calgary, AB", "CA"),
        ("Toronto ON", "CA"),
        ("Montreal, Quebec", "CA"),
        ("Halifax NS", "CA"),
        ("", None),
    ],
)
def test_infer_country(note, expected):
    assert infer_country(note) == expected


@pytest.mark.django_db
def test_seed_aliases_creates_manual_aliases(subreddit_places):
    written = seed_aliases(ALIASES_CSV)

    assert written == 1
    alias = PlaceAlias.objects.get(key="slc")
    assert alias.place == subreddit_places["salt_lake_city"]
    assert alias.source == "manual"
    assert alias.kind == "nickname"

    assert seed_aliases(ALIASES_CSV) == 1
    assert PlaceAlias.objects.filter(key="slc").count() == 1


@pytest.mark.django_db
def test_command_seeds_subreddits_and_aliases(subreddit_places):
    stdout = StringIO()

    call_command(
        "seed_watch_subreddits",
        subreddits_csv=str(SUBREDDITS_CSV),
        aliases_csv=str(ALIASES_CSV),
        stdout=stdout,
    )

    assert Subreddit.objects.count() == 63
    assert PlaceAlias.objects.filter(key="slc", source="manual").exists()
    assert "created=63" in stdout.getvalue()


@pytest.mark.django_db
def test_command_refresh_labels_flag(subreddit_places):
    seed_subreddits(SUBREDDITS_CSV)
    Subreddit.objects.filter(name="chicoca").update(kind="general", note="operator")
    stdout = StringIO()

    call_command(
        "seed_watch_subreddits",
        subreddits_csv=str(SUBREDDITS_CSV),
        aliases_csv=str(ALIASES_CSV),
        refresh_labels=True,
        stdout=stdout,
    )

    row = Subreddit.objects.get(name="chicoca")
    assert row.kind == "place"
    assert row.note == ""
    assert "updated=63" in stdout.getvalue()


@pytest.mark.django_db
def test_seed_timestamps_are_consistent(subreddit_places):
    before = timezone.now()

    seed_subreddits(SUBREDDITS_CSV)

    row = Subreddit.objects.get(name="chicoca")
    assert row.reviewed_at is not None
    assert row.reviewed_at >= before
