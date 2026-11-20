import csv
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import CommandError, call_command
from openpyxl import Workbook

from watch.gazetteer import (
    GOVERNMENT_FILENAME,
    GovernmentError,
    government_key,
    load_government_rows,
    load_governments,
)
from watch.models import Place

FIXTURES = Path(__file__).parent / "fixtures"

GP_HEADERS = (
    "UNIT_NAME",
    "UNIT_TYPE",
    "POPULATION",
    "FIPS_STATE",
    "FIPS_COUNTY",
    "FIPS_PLACE",
    "IS_ACTIVE",
)
SD_HEADERS = ("UNIT_NAME", "FIPS_STATE", "FIPS_COUNTY", "IS_ACTIVE")


@pytest.fixture
def government_places(db):
    county = Place.objects.create(
        canonical_key="us:county:06001",
        geoid="06001",
        name="Alameda",
        legal_name="Alameda County",
        place_type=Place.PlaceType.COUNTY,
        state="CA",
    )
    Place.objects.create(
        canonical_key="us:place:0600001",
        geoid="0600001",
        name="Albany",
        place_type=Place.PlaceType.CITY,
        state="CA",
    )
    Place.objects.create(
        canonical_key="us:cousub:0911024800",
        geoid="0911024800",
        name="East Windsor town",
        place_type=Place.PlaceType.COUNTY_SUBDIVISION,
        state="CT",
    )
    Place.objects.create(
        canonical_key="us:school:0600001",
        geoid="0600001",
        name="Abbeville Unified",
        legal_name="Abbeville Unified School District",
        place_type=Place.PlaceType.SCHOOL_DISTRICT,
        state="CA",
    )
    # Two same-named districts make a school match ambiguous.
    for branch in ("north", "south"):
        Place.objects.create(
            canonical_key=f"us:school:lincoln-{branch}",
            name="Lincoln Unified",
            place_type=Place.PlaceType.SCHOOL_DISTRICT,
            state="CA",
        )
    return county


def write_workbook(path):
    workbook = Workbook()
    general = workbook.active
    general.title = "General Purpose"
    general.append(GP_HEADERS)
    general.append(
        (
            "COUNTY OF ALAMEDA",
            "1 - COUNTY",
            1629540,
            "06",
            "001",
            "99001",
            "Y",
        )
    )
    general.append(
        ("CITY OF ALBANY", "2 - MUNICIPAL", 20271, "06", "001", "00001", "Y")
    )
    general.append(
        ("CITY OF OLDTOWN", "2 - MUNICIPAL", 1000, "06", "001", "00002", "N")
    )

    school = workbook.create_sheet("School District")
    school.append(SD_HEADERS)
    school.append(("ABBEVILLE UNIFIED SCHOOL DISTRICT", "06", "001", "Y"))

    special = workbook.create_sheet("Special District")
    special.append(("UNIT_NAME", "FUNCTION_NAME", "FIPS_STATE", "IS_ACTIVE"))
    special.append(("PRATTVILLE HOUSING AUTHORITY", "50 - HOUSING", "01", "Y"))

    dependent = workbook.create_sheet("DEP School Dist")
    dependent.append(("UNIT_NAME", "FIPS_STATE", "IS_ACTIVE"))
    dependent.append(("KASHUNAMIUT SCHOOL DISTRICT", "02", "Y"))

    workbook.save(path)
    return path


def read_government_csv(path):
    with path.open(newline="", encoding="utf-8") as handle:
        return [
            {key.lower(): value for key, value in row.items()}
            for row in csv.DictReader(handle)
        ]


def test_government_key_builds_namespaced_keys():
    assert government_key("1 - COUNTY", "06", "001", "99001") == "us:county:06001"
    assert government_key("2 - MUNICIPAL", "06", "001", "00001") == "us:place:0600001"
    assert (
        government_key("3 - TOWNSHIP", "09", "110", "24800") == "us:cousub:0911024800"
    )
    assert government_key("9 - SPECIAL DISTRICT", "06", "001", "00001") is None


@pytest.mark.django_db
def test_government_rows_mark_counties_municipalities_townships(government_places):
    stats = load_government_rows(
        [
            {
                "unit_type": "1 - COUNTY",
                "fips_state": "06",
                "fips_county": "001",
                "fips_place": "99001",
                "unit_name": "COUNTY OF ALAMEDA",
                "population": "1629540",
                "is_active": "Y",
            },
            {
                "unit_type": "2 - MUNICIPAL",
                "fips_state": "06",
                "fips_county": "001",
                "fips_place": "00001",
                "unit_name": "CITY OF ALBANY",
                "population": "20271",
                "is_active": "Y",
            },
            {
                "unit_type": "3 - TOWNSHIP",
                "fips_state": "09",
                "fips_county": "110",
                "fips_place": "24800",
                "unit_name": "TOWN OF EAST WINDSOR",
                "population": "12345",
                "is_active": "Y",
            },
        ]
    )
    assert stats == {
        "county": 1,
        "municipal": 1,
        "township": 1,
        "school": 0,
        "unmatched_school": 0,
        "skipped": 0,
    }
    assert Place.objects.get(canonical_key="us:county:06001").is_government is True
    assert Place.objects.get(canonical_key="us:place:0600001").population == 20271
    county = Place.objects.get(canonical_key="us:place:0600001").county
    assert county is not None
    assert county.name == "Alameda"


@pytest.mark.django_db
def test_inactive_units_are_skipped(government_places):
    stats = load_government_rows(
        [
            {
                "unit_type": "2 - MUNICIPAL",
                "is_active": "N",
                "fips_state": "06",
                "fips_place": "00001",
            }
        ]
    )
    assert stats["skipped"] == 1


@pytest.mark.django_db
def test_rows_without_a_matching_place_are_skipped(government_places):
    stats = load_government_rows(
        [
            {
                "unit_type": "2 - MUNICIPAL",
                "fips_state": "06",
                "fips_county": "001",
                "fips_place": "99999",
                "population": "5000",
                "is_active": "Y",
            },
            {
                "unit_type": "9 - SPECIAL DISTRICT",
                "fips_state": "06",
                "fips_county": "001",
                "is_active": "Y",
            },
        ]
    )

    assert stats["skipped"] == 2
    assert stats["municipal"] == 0


@pytest.mark.django_db
def test_loader_pads_numeric_fips_components(government_places):
    stats = load_government_rows(
        [
            {
                "unit_type": "2 - MUNICIPAL",
                "fips_state": "6",
                "fips_county": "1",
                "fips_place": "1",
                "population": 100,
                "is_active": "Y",
            }
        ]
    )

    assert stats["municipal"] == 1
    assert Place.objects.get(canonical_key="us:place:0600001").population == 100


@pytest.mark.django_db
def test_non_positive_population_is_not_stored(government_places):
    load_government_rows(
        [
            {
                "unit_type": "2 - MUNICIPAL",
                "fips_state": "06",
                "fips_place": "00001",
                "population": "0",
                "is_active": "Y",
            }
        ]
    )

    assert Place.objects.get(canonical_key="us:place:0600001").population is None


@pytest.mark.django_db
def test_school_districts_match_by_normalized_name(government_places):
    stats = load_government_rows(
        [
            {
                "unit_type": "SCHOOL DISTRICT",
                "fips_state": "06",
                "fips_county": "001",
                "unit_name": "ABBEVILLE UNIFIED SCHOOL DISTRICT",
                "population": "",
                "is_active": "Y",
            }
        ]
    )

    assert stats["school"] == 1
    assert stats["unmatched_school"] == 0
    school = Place.objects.get(canonical_key="us:school:0600001")
    assert school.is_government is True
    county = school.county
    assert county is not None
    assert county.name == "Alameda"


@pytest.mark.django_db
def test_school_districts_count_unmatched_names(government_places):
    stats = load_government_rows(
        [
            {
                "unit_type": "SCHOOL DISTRICT",
                "fips_state": "06",
                "fips_county": "001",
                "unit_name": "NOWHERE SCHOOL DISTRICT",
                "is_active": "Y",
            },
            {
                "unit_type": "SCHOOL DISTRICT",
                "fips_state": "06",
                "fips_county": "001",
                "unit_name": "LINCOLN UNIFIED SCHOOL DISTRICT",
                "is_active": "Y",
            },
            {
                "unit_type": "SCHOOL DISTRICT",
                "fips_state": "32",
                "unit_name": "ABBEVILLE UNIFIED SCHOOL DISTRICT",
                "is_active": "Y",
            },
        ]
    )

    assert stats["school"] == 0
    assert stats["unmatched_school"] == 3


@pytest.mark.django_db
def test_csv_fixture_loads_known_rows(government_places):
    stats = load_government_rows(read_government_csv(FIXTURES / "governments.csv"))

    assert stats == {
        "county": 1,
        "municipal": 1,
        "township": 0,
        "school": 0,
        "unmatched_school": 0,
        "skipped": 2,
    }
    assert Place.objects.get(canonical_key="us:county:06001").is_government is True


@pytest.mark.django_db
def test_load_governments_reads_general_purpose_and_school_sheets(
    tmp_path, government_places
):
    workbook_path = write_workbook(tmp_path / GOVERNMENT_FILENAME)

    stats = load_governments(workbook_path)

    assert stats == {
        "county": 1,
        "municipal": 1,
        "township": 0,
        "school": 1,
        "unmatched_school": 0,
        "skipped": 1,
    }
    assert Place.objects.get(canonical_key="us:county:06001").population == 1629540
    assert Place.objects.get(canonical_key="us:place:0600001").is_government is True
    assert Place.objects.get(canonical_key="us:school:0600001").is_government is True


def test_load_governments_requires_both_sheets(tmp_path):
    workbook = Workbook()
    workbook.active.title = "General Purpose"
    path = tmp_path / GOVERNMENT_FILENAME
    workbook.save(path)

    with pytest.raises(GovernmentError, match="School District"):
        load_governments(path)


@pytest.mark.django_db
def test_command_loads_file(tmp_path, government_places):
    workbook_path = write_workbook(tmp_path / "units.xlsx")
    stdout = StringIO()

    call_command("load_watch_governments", "--file", str(workbook_path), stdout=stdout)

    assert "county=1" in stdout.getvalue()
    assert "school=1" in stdout.getvalue()


@pytest.mark.django_db
def test_command_loads_source_dir(tmp_path, government_places):
    write_workbook(tmp_path / GOVERNMENT_FILENAME)
    stdout = StringIO()

    call_command("load_watch_governments", "--source-dir", str(tmp_path), stdout=stdout)

    assert "municipal=1" in stdout.getvalue()


def test_command_rejects_file_with_source_dir(tmp_path):
    with pytest.raises(
        CommandError, match="--file cannot be combined with --source-dir"
    ):
        call_command(
            "load_watch_governments",
            "--file",
            str(tmp_path / "units.xlsx"),
            "--source-dir",
            str(tmp_path),
        )


def test_command_raises_when_file_is_missing(tmp_path):
    with pytest.raises(CommandError, match="not found"):
        call_command("load_watch_governments", "--file", str(tmp_path / "missing.xlsx"))
