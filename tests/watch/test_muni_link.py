"""Tests for linking civic.band municipalities to gazetteer places."""

from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from tests.factories import MuniFactory, PlaceFactory
from watch.models import Place, PlaceAlias
from watch.muni_link import link_munis, match_muni


def alias(place, key, kind=PlaceAlias.Kind.NAME):
    return PlaceAlias.objects.create(
        place=place,
        key=key,
        kind=kind,
        source=PlaceAlias.Source.MANUAL,
    )


@pytest.mark.django_db
def test_link_muni_by_name_and_state():
    muni = MuniFactory(name="Chico", state="CA")
    place = PlaceFactory(name="Chico", state="CA", canonical_key="us:place:0613010")

    stats = link_munis()

    place.refresh_from_db()
    assert stats == {"linked": 1, "ambiguous": 0, "no_match": 0, "already": 0}
    assert place.muni == muni
    assert place.muni_link_status == "auto"
    assert place.muni_reviewed_at is not None


@pytest.mark.django_db
def test_link_leaves_ambiguous_muni_in_review():
    muni = MuniFactory(name="Glendale", state="CA")
    PlaceFactory(name="Glendale", state="AZ", canonical_key="us:place:0427824")
    PlaceFactory(name="Glendale", state="CA", canonical_key="us:place:0630000")

    stats = link_munis()

    assert stats["linked"] == 0
    assert stats["ambiguous"] == 1
    assert Place.objects.filter(muni=muni).count() == 0


@pytest.mark.django_db
def test_alias_key_wins_before_place_name():
    muni = MuniFactory(name="SF", state="CA")
    place = PlaceFactory(
        name="San Francisco", state="CA", canonical_key="us:place:0667000"
    )
    alias(place, "sfca", PlaceAlias.Kind.NAME_STATE)

    matched, status = match_muni(muni)

    assert (matched, status) == (place, "auto")


@pytest.mark.django_db
def test_alias_key_breaks_a_name_tie():
    muni = MuniFactory(name="Glendale", state="CA")
    az = PlaceFactory(name="Glendale", state="AZ", canonical_key="us:place:0427824")
    PlaceFactory(name="Glendale", state="CA", canonical_key="us:place:0630000")
    # An operator can point name+state at a non-obvious gazetteer row.
    alias(az, "glendaleca", PlaceAlias.Kind.NAME_STATE)

    matched, status = match_muni(muni)

    assert (matched, status) == (az, "auto")


@pytest.mark.django_db
def test_coordinates_choose_the_closest_name_match():
    muni = MuniFactory(
        name="Springfield", state="CA", latitude=39.72, longitude=-121.84
    )
    near = PlaceFactory(
        name="Springfield",
        state="CA",
        canonical_key="us:place:0600001",
        latitude=39.71,
        longitude=-121.83,
    )
    PlaceFactory(
        name="Springfield",
        state="OR",
        canonical_key="us:place:4100001",
        latitude=44.05,
        longitude=-123.02,
    )

    matched, status = match_muni(muni)

    assert (matched, status) == (near, "auto")


@pytest.mark.django_db
def test_coordinates_farther_than_a_quarter_degree_stay_ambiguous():
    muni = MuniFactory(
        name="Springfield", state="CA", latitude=39.72, longitude=-121.84
    )
    PlaceFactory(
        name="Springfield",
        state="CA",
        canonical_key="us:place:0600001",
        latitude=40.5,
        longitude=-122.5,
    )
    PlaceFactory(
        name="Springfield",
        state="OR",
        canonical_key="us:place:4100001",
        latitude=44.05,
        longitude=-123.02,
    )

    matched, status = match_muni(muni)

    assert matched is None
    assert status == "ambiguous"


@pytest.mark.django_db
def test_missing_coordinates_cannot_break_a_tie():
    muni = MuniFactory(
        name="Springfield", state="CA", latitude=39.72, longitude=-121.84
    )
    PlaceFactory(name="Springfield", state="CA", canonical_key="us:place:0600001")
    PlaceFactory(name="Springfield", state="OR", canonical_key="us:place:4100001")

    matched, status = match_muni(muni)

    assert (matched, status) == (None, "ambiguous")


@pytest.mark.django_db
def test_no_match_when_nothing_shares_the_name():
    MuniFactory(name="Atlantis", state="CA")
    PlaceFactory(name="Chico", state="CA", canonical_key="us:place:0613010")

    stats = link_munis()

    assert stats["no_match"] == 1
    assert stats["linked"] == 0


@pytest.mark.django_db
def test_confirmed_places_are_never_overwritten():
    other = MuniFactory(name="Other", state="CA")
    confirmed = PlaceFactory(
        name="Atlantis",
        state="CA",
        canonical_key="us:place:0600002",
        muni=other,
        muni_link_status="confirmed",
    )
    MuniFactory(name="Atlantis", state="CA")

    stats = link_munis()

    confirmed.refresh_from_db()
    assert stats["no_match"] == 1
    assert stats["linked"] == 0
    assert confirmed.muni == other
    assert confirmed.muni_link_status == "confirmed"


@pytest.mark.django_db
def test_muni_with_a_confirmed_place_is_counted_as_already():
    muni = MuniFactory(name="Chico", state="CA")
    confirmed = PlaceFactory(
        name="Chico",
        state="CA",
        canonical_key="us:place:0613010",
        muni=muni,
        muni_link_status="confirmed",
        muni_reviewed_at=timezone.now(),
    )

    stats = link_munis()

    confirmed.refresh_from_db()
    assert stats["already"] == 1
    assert stats["linked"] == 0
    assert confirmed.muni_link_status == "confirmed"


@pytest.mark.django_db
def test_muni_rows_are_never_written():
    muni = MuniFactory(name="Chico", state="CA")
    before = muni.modified

    link_munis()

    muni.refresh_from_db()
    assert muni.modified == before
    assert muni.name == "Chico"
    assert muni.state == "CA"


@pytest.mark.django_db
def test_dry_run_reports_without_saving():
    MuniFactory(name="Chico", state="CA")
    place = PlaceFactory(name="Chico", state="CA", canonical_key="us:place:0613010")

    stats = link_munis(dry_run=True)

    place.refresh_from_db()
    assert stats["linked"] == 1
    assert place.muni is None
    assert place.muni_link_status == "unlinked"


@pytest.mark.django_db
def test_command_links_and_prints_counts():
    muni = MuniFactory(name="Chico", state="CA")
    place = PlaceFactory(name="Chico", state="CA", canonical_key="us:place:0613010")
    out = StringIO()

    call_command("link_watch_munis", stdout=out)

    place.refresh_from_db()
    assert place.muni == muni
    assert "linked=1" in out.getvalue()


@pytest.mark.django_db
def test_command_dry_run_does_not_save():
    MuniFactory(name="Chico", state="CA")
    place = PlaceFactory(name="Chico", state="CA", canonical_key="us:place:0613010")
    out = StringIO()

    call_command("link_watch_munis", "--dry-run", stdout=out)

    place.refresh_from_db()
    assert place.muni is None
    assert out.getvalue().startswith("dry-run ")
    assert "linked=1" in out.getvalue()


@pytest.mark.django_db
def test_command_limit_processes_only_the_first_munis():
    MuniFactory(name="Chico", state="CA")
    PlaceFactory(name="Chico", state="CA", canonical_key="us:place:0613010")
    MuniFactory(name="Durham", state="NC")
    durham = PlaceFactory(name="Durham", state="NC", canonical_key="us:place:3719000")
    out = StringIO()

    call_command("link_watch_munis", "--limit", "1", stdout=out)

    durham.refresh_from_db()
    assert durham.muni is None
    assert "linked=1" in out.getvalue()
