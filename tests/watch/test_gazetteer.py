from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command

from watch.gazetteer import build_aliases, load_gazetteer, refresh_aliases
from watch.models import Place, PlaceAlias

FIXTURES = Path(__file__).parent / "fixtures" / "gazetteer"


@pytest.fixture
def gazetteer_dir():
    return FIXTURES


@pytest.mark.django_db
def test_loader_creates_typed_places_and_is_idempotent(gazetteer_dir):
    first = load_gazetteer(gazetteer_dir)
    second = load_gazetteer(gazetteer_dir)
    assert first == second
    assert (
        Place.objects.count()
        == first["state"]
        + first["county"]
        + first["cousub"]
        + first["place"]
        + first["school"]
    )
    assert (
        Place.objects.get(geoid="0600001", place_type="city").canonical_key
        == "us:place:0600001"
    )


@pytest.mark.django_db
def test_same_geoid_different_types_are_distinct(gazetteer_dir):
    load_gazetteer(gazetteer_dir)
    assert Place.objects.filter(geoid="0600001").count() == 2


@pytest.mark.django_db
def test_aliases_include_state_and_type_keys(gazetteer_dir):
    load_gazetteer(gazetteer_dir)
    keys = set(
        PlaceAlias.objects.filter(place__name="Abbeville").values_list("key", flat=True)
    )
    assert {"abbeville", "abbevilleal", "abbevillealabama", "abbevillecity"} <= keys


@pytest.mark.django_db
def test_loader_cleans_names_and_keeps_legal_name_variant(gazetteer_dir):
    load_gazetteer(gazetteer_dir)

    city = Place.objects.get(canonical_key="us:place:0600001")
    assert city.name == "Abbeville"
    assert city.place_type == Place.PlaceType.CITY
    assert city.state == "CA"
    assert city.country == "US"
    assert city.latitude == pytest.approx(32.477138)
    assert city.longitude == pytest.approx(-82.968027)
    assert city.aliases.filter(key="abbevillecity", kind="variant").exists()

    county = Place.objects.get(canonical_key="us:county:01001")
    assert county.name == "Autauga"
    assert county.place_type == Place.PlaceType.COUNTY
    assert county.aliases.filter(key="autaugacounty", kind="name_county").exists()

    subdivision = Place.objects.get(canonical_key="us:cousub:0100190805")
    assert subdivision.name == "Autauga Valley"
    assert subdivision.place_type == Place.PlaceType.COUNTY_SUBDIVISION

    school = Place.objects.get(canonical_key="us:school:0600001")
    assert school.name == "Abbeville Unified"
    assert school.place_type == Place.PlaceType.SCHOOL_DISTRICT


@pytest.mark.django_db
def test_refresh_aliases_preserves_manual_aliases(gazetteer_dir):
    load_gazetteer(gazetteer_dir)
    city = Place.objects.get(canonical_key="us:place:0600001")
    PlaceAlias.objects.create(
        place=city, key="abbeyville", kind="nickname", source="manual"
    )
    before = set(city.aliases.values_list("key", "kind"))

    created = refresh_aliases(city)

    assert created == (
        PlaceAlias.objects.filter(place=city).exclude(source="manual").count()
    )
    assert city.aliases.filter(key="abbeyville", source="manual").exists()
    assert set(city.aliases.values_list("key", "kind")) == before | {
        ("abbeyville", "nickname")
    }
    assert city.aliases.count() == PlaceAlias.objects.filter(place=city).count()


@pytest.mark.django_db
def test_build_aliases_returns_unsaved_aliases(gazetteer_dir):
    load_gazetteer(gazetteer_dir)
    city = Place.objects.get(canonical_key="us:place:0600001")

    aliases = build_aliases(city)

    assert aliases
    assert all(alias.pk is None for alias in aliases)
    assert all(alias.source == PlaceAlias.Source.GAZETTEER for alias in aliases)
    assert {alias.kind for alias in aliases} == {
        PlaceAlias.Kind.NAME,
        PlaceAlias.Kind.NAME_STATE,
        PlaceAlias.Kind.NAME_STATE_NAME,
        PlaceAlias.Kind.NAME_CITY,
    }


@pytest.mark.django_db
def test_command_loads_source_dir(gazetteer_dir):
    stdout = StringIO()
    call_command(
        "load_watch_gazetteer", "--source-dir", str(gazetteer_dir), stdout=stdout
    )
    assert Place.objects.count() == 8
    assert "place=2" in stdout.getvalue()


@pytest.mark.django_db
def test_command_dry_run_rolls_back(gazetteer_dir):
    stdout = StringIO()
    call_command(
        "load_watch_gazetteer",
        "--source-dir",
        str(gazetteer_dir),
        "--dry-run",
        stdout=stdout,
    )
    assert Place.objects.count() == 0
    assert PlaceAlias.objects.count() == 0
    assert "dry run" in stdout.getvalue().lower()
