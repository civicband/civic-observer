import zipfile
from io import StringIO
from pathlib import Path

import pytest
from django.conf import settings
from django.core.management import CommandError, call_command

from watch.gazetteer import (
    GazetteerError,
    _extract_zip,
    build_aliases,
    load_gazetteer,
    refresh_aliases,
)
from watch.models import Place, PlaceAlias

FIXTURES = Path(__file__).parent / "fixtures" / "gazetteer"
TAB_FIXTURES = Path(__file__).parent / "fixtures" / "gazetteer_tab"


@pytest.fixture
def gazetteer_dir():
    return FIXTURES


def alias_keys(place):
    return set(place.aliases.values_list("key", flat=True))


@pytest.mark.django_db
def test_loader_creates_typed_places_and_is_idempotent(gazetteer_dir):
    first = load_gazetteer(gazetteer_dir)
    second = load_gazetteer(gazetteer_dir)
    assert first == second
    assert first == {
        "state": 6,
        "county": 5,
        "cousub": 2,
        "place": 3,
        "school": 3,
    }
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
def test_aliases_are_generated_per_place(gazetteer_dir):
    load_gazetteer(gazetteer_dir)

    city = Place.objects.get(canonical_key="us:place:0600001")
    assert alias_keys(city) == {
        "abbeville",
        "abbevilleca",
        "abbevillecalifornia",
        "abbevillecity",
    }

    cdp = Place.objects.get(canonical_key="us:place:0100428")
    assert alias_keys(cdp) == {
        "abbeville",
        "abbevilleal",
        "abbevillealabama",
        "abbevillecdp",
    }


@pytest.mark.django_db
def test_loader_cleans_place_names_and_records_legal_name(gazetteer_dir):
    load_gazetteer(gazetteer_dir)

    city = Place.objects.get(canonical_key="us:place:0600001")
    assert city.name == "Abbeville"
    assert city.legal_name == "Abbeville city"
    assert city.place_type == Place.PlaceType.CITY
    assert city.state == "CA"
    assert city.country == "US"
    assert city.latitude == pytest.approx(32.477138)
    assert city.longitude == pytest.approx(-82.968027)
    assert city.aliases.filter(key="abbevillecity", kind="variant").exists()

    school = Place.objects.get(canonical_key="us:school:0600001")
    assert school.name == "Abbeville Unified"
    assert school.legal_name == "Abbeville Unified School District"
    assert school.place_type == Place.PlaceType.SCHOOL_DISTRICT


@pytest.mark.django_db
def test_county_and_subdivision_names_keep_official_suffixes(gazetteer_dir):
    load_gazetteer(gazetteer_dir)

    expected = {
        "us:county:24510": ("Baltimore city", Place.PlaceType.COUNTY),
        "us:county:32510": ("Carson City", Place.PlaceType.COUNTY),
        "us:county:22001": ("Acadia Parish", Place.PlaceType.COUNTY),
        "us:cousub:0900300001": (
            "East Windsor town",
            Place.PlaceType.COUNTY_SUBDIVISION,
        ),
    }
    for key, (name, place_type) in expected.items():
        place = Place.objects.get(canonical_key=key)
        assert place.name == name
        assert place.legal_name == name
        assert place.place_type == place_type


@pytest.mark.django_db
def test_county_and_subdivision_aliases_include_stripped_bases(gazetteer_dir):
    load_gazetteer(gazetteer_dir)

    baltimore = Place.objects.get(canonical_key="us:county:24510")
    assert {
        "baltimore",
        "baltimorecity",
        "baltimoremd",
        "baltimorecitymd",
        "baltimoremaryland",
        "baltimorecitymaryland",
    } <= alias_keys(baltimore)

    carson = Place.objects.get(canonical_key="us:county:32510")
    assert {"carson", "carsoncity", "carsonnv"} <= alias_keys(carson)

    acadia = Place.objects.get(canonical_key="us:county:22001")
    assert {"acadia", "acadiaparish", "acadiala"} <= alias_keys(acadia)

    east_windsor = Place.objects.get(canonical_key="us:cousub:0900300001")
    assert {
        "eastwindsor",
        "eastwindsortown",
        "eastwindsorct",
        "eastwindsortownct",
    } <= alias_keys(east_windsor)


@pytest.mark.django_db
def test_unknown_place_lsad_keeps_official_name(gazetteer_dir):
    load_gazetteer(gazetteer_dir)

    place = Place.objects.get(canonical_key="us:place:0600004")
    assert place.name == "Mystery City"
    assert place.legal_name == "Mystery City"
    assert place.place_type == Place.PlaceType.CITY
    assert alias_keys(place) == {
        "mysterycity",
        "mysterycityca",
        "mysterycitycalifornia",
    }


@pytest.mark.django_db
def test_all_school_files_load_with_distinct_geoids(gazetteer_dir):
    counts = load_gazetteer(gazetteer_dir)

    assert counts["school"] == 3
    for geoid in ("0600001", "0600002", "0600003"):
        assert (
            Place.objects.get(
                geoid=geoid, place_type=Place.PlaceType.SCHOOL_DISTRICT
            ).canonical_key
            == f"us:school:{geoid}"
        )


@pytest.mark.django_db
def test_alias_set_is_stable_across_loads(gazetteer_dir):
    load_gazetteer(gazetteer_dir)
    first = set(
        PlaceAlias.objects.filter(source="gazetteer").values_list(
            "place_id", "key", "kind"
        )
    )

    load_gazetteer(gazetteer_dir)

    second = set(
        PlaceAlias.objects.filter(source="gazetteer").values_list(
            "place_id", "key", "kind"
        )
    )
    assert second == first


@pytest.mark.django_db
def test_manual_aliases_survive_second_load(gazetteer_dir):
    load_gazetteer(gazetteer_dir)
    city = Place.objects.get(canonical_key="us:place:0600001")
    preserved = [
        PlaceAlias.objects.create(
            place=city, key="abbeyville", kind="nickname", source="manual"
        ),
        PlaceAlias.objects.create(
            place=city, key="abbeyvillesc", kind="variant", source="geonames"
        ),
        PlaceAlias.objects.create(
            place=city, key="abbey", kind="nickname", source="wikidata"
        ),
    ]
    generated_before = set(
        city.aliases.filter(source="gazetteer").values_list("key", "kind")
    )

    load_gazetteer(gazetteer_dir)

    assert PlaceAlias.objects.filter(
        pk__in=[alias.pk for alias in preserved]
    ).count() == len(preserved)
    assert set(city.aliases.filter(source="gazetteer").values_list("key", "kind")) == (
        generated_before
    )


@pytest.mark.django_db
def test_refresh_aliases_preserves_non_gazetteer_aliases(gazetteer_dir):
    load_gazetteer(gazetteer_dir)
    city = Place.objects.get(canonical_key="us:place:0600001")
    PlaceAlias.objects.create(
        place=city, key="abbeyville", kind="nickname", source="manual"
    )
    PlaceAlias.objects.create(
        place=city, key="abbeyvillesc", kind="variant", source="geonames"
    )
    city.aliases.filter(key="abbeville", kind="name", source="gazetteer").delete()
    PlaceAlias.objects.create(
        place=city, key="abbeville", kind="name", source="wikidata"
    )
    generated_before = set(
        city.aliases.filter(source="gazetteer").values_list("key", "kind")
    )

    created = refresh_aliases(city)

    assert created == city.aliases.filter(source="gazetteer").count()
    assert city.aliases.filter(key="abbeyville", source="manual").exists()
    assert city.aliases.filter(key="abbeyvillesc", source="geonames").exists()
    assert city.aliases.filter(key="abbeville", source="wikidata").exists()
    assert not city.aliases.filter(
        key="abbeville", kind="name", source="gazetteer"
    ).exists()
    assert set(city.aliases.filter(source="gazetteer").values_list("key", "kind")) == (
        generated_before - {("abbeville", "name")}
    )


@pytest.mark.django_db
def test_build_aliases_returns_unsaved_aliases_with_variant(gazetteer_dir):
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
        PlaceAlias.Kind.VARIANT,
    }
    variant = next(alias for alias in aliases if alias.kind == PlaceAlias.Kind.VARIANT)
    assert variant.key == "abbevillecity"


@pytest.mark.django_db
def test_command_loads_source_dir(gazetteer_dir):
    stdout = StringIO()
    stderr = StringIO()
    call_command(
        "load_watch_gazetteer",
        "--source-dir",
        str(gazetteer_dir),
        stdout=stdout,
        stderr=stderr,
    )
    assert Place.objects.count() == 19
    assert "place=3" in stdout.getvalue()
    assert stderr.getvalue() == ""


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


def test_command_rejects_download_with_source_dir(gazetteer_dir):
    with pytest.raises(
        CommandError, match="--download cannot be combined with --source-dir"
    ):
        call_command(
            "load_watch_gazetteer",
            "--download",
            "--source-dir",
            str(gazetteer_dir),
        )


def test_command_raises_when_no_files_exist(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "WATCH_DATA_DIR", str(tmp_path / "cache"))

    with pytest.raises(CommandError, match="No Gazetteer TSV files found"):
        call_command("load_watch_gazetteer")


@pytest.mark.django_db
def test_command_reports_missing_files(tmp_path):
    source = tmp_path / "gazetteer"
    source.mkdir()
    (source / "2026_Gaz_state_national.txt").write_text(
        (FIXTURES / "2026_Gaz_state_national.txt").read_text()
    )
    stdout = StringIO()
    stderr = StringIO()

    call_command(
        "load_watch_gazetteer",
        "--source-dir",
        str(source),
        stdout=stdout,
        stderr=stderr,
    )

    assert "Missing Gazetteer files" in stderr.getvalue()
    assert "counties" in stderr.getvalue()
    assert "state=6" in stdout.getvalue()


@pytest.mark.django_db
def test_tab_delimited_file_parses():
    counts = load_gazetteer(TAB_FIXTURES)
    assert counts["state"] == 1


@pytest.mark.django_db
def test_leading_blank_lines_are_ignored(tmp_path):
    content = (FIXTURES / "2026_Gaz_state_national.txt").read_text()
    (tmp_path / "2026_Gaz_state_national.txt").write_text("\n\n" + content)

    counts = load_gazetteer(tmp_path)

    assert counts["state"] == 6


def test_unparseable_file_raises(tmp_path):
    (tmp_path / "2026_Gaz_state_national.txt").write_text("GEOID,NAME\n0600001,Foo\n")

    with pytest.raises(GazetteerError, match="delimiter"):
        load_gazetteer(tmp_path)


def test_extract_zip_flattens_files_and_skips_hidden(tmp_path):
    archive = tmp_path / "gazetteer.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("nested/2026_Gaz_state_national.txt", "state data")
        zipped.writestr(".DS_Store", "junk")
    target = tmp_path / "extracted"

    written = _extract_zip(archive, target)

    assert written == [target / "2026_Gaz_state_national.txt"]
    assert written[0].read_text() == "state data"
    assert not (target / ".DS_Store").exists()
