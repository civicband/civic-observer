from io import StringIO
from pathlib import Path

import pytest
from django.core.management import CommandError, call_command

from watch.gazetteer import load_geonames
from watch.models import NonUsCity, NonUsCityAlias, Place, PlaceAlias

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def cities500_file():
    return FIXTURES / "cities500_sample.txt"


@pytest.fixture
def cities500_dir(tmp_path, cities500_file):
    directory = tmp_path / "geonames"
    directory.mkdir()
    (directory / "cities500.txt").write_bytes(cities500_file.read_bytes())
    return directory


@pytest.fixture
def us_places(db):
    """Create the counties and places used by the US GeoNames rows."""
    henry = Place.objects.create(
        canonical_key="us:county:01067",
        geoid="01067",
        name="Henry",
        legal_name="Henry County",
        place_type=Place.PlaceType.COUNTY,
        state="AL",
    )
    sangamon = Place.objects.create(
        canonical_key="us:county:17167",
        geoid="17167",
        name="Sangamon",
        legal_name="Sangamon County",
        place_type=Place.PlaceType.COUNTY,
        state="IL",
    )
    jefferson = Place.objects.create(
        canonical_key="us:county:01073",
        geoid="01073",
        name="Jefferson",
        legal_name="Jefferson County",
        place_type=Place.PlaceType.COUNTY,
        state="AL",
    )
    alameda = Place.objects.create(
        canonical_key="us:county:06001",
        geoid="06001",
        name="Alameda",
        legal_name="Alameda County",
        place_type=Place.PlaceType.COUNTY,
        state="CA",
    )

    abbeville = Place.objects.create(
        canonical_key="us:place:0100001",
        geoid="0100001",
        name="Abbeville",
        place_type=Place.PlaceType.CITY,
        state="AL",
        county=alameda,
    )
    fairview = Place.objects.create(
        canonical_key="us:place:0100002",
        geoid="0100002",
        name="Fairview",
        place_type=Place.PlaceType.CITY,
        state="AL",
    )
    birmingham = Place.objects.create(
        canonical_key="us:place:0100003",
        geoid="0100003",
        name="Birmingham",
        place_type=Place.PlaceType.CITY,
        state="AL",
    )
    springfield_city = Place.objects.create(
        canonical_key="us:place:1700001",
        geoid="1700001",
        name="Springfield",
        place_type=Place.PlaceType.CITY,
        state="IL",
    )
    springfield_village = Place.objects.create(
        canonical_key="us:place:1700002",
        geoid="1700002",
        name="Springfield",
        place_type=Place.PlaceType.VILLAGE,
        state="IL",
    )
    nowhere = Place.objects.create(
        canonical_key="us:place:0100004",
        geoid="0100004",
        name="Nowhere",
        place_type=Place.PlaceType.CITY,
        state="AL",
    )
    return {
        "henry": henry,
        "sangamon": sangamon,
        "jefferson": jefferson,
        "alameda": alameda,
        "abbeville": abbeville,
        "fairview": fairview,
        "birmingham": birmingham,
        "springfield_city": springfield_city,
        "springfield_village": springfield_village,
        "nowhere": nowhere,
    }


def alias_keys(place):
    return set(place.aliases.values_list("key", flat=True))


@pytest.mark.django_db
def test_canadian_city_becomes_place_and_non_us_city_is_lookup(cities500_file):
    load_geonames(cities500_file)

    place = Place.objects.get(canonical_key="ca:city:6094817")
    assert place.place_type == "canadian_city"
    assert place.name == "Ottawa"
    assert place.legal_name == "Ottawa"
    assert place.country == "CA"
    assert place.state == "ON"
    assert place.population == 812129
    assert place.latitude == pytest.approx(45.41117)
    assert place.longitude == pytest.approx(-75.69812)

    assert NonUsCity.objects.get(geoname_id=2643743).country == "GB"
    assert NonUsCityAlias.objects.filter(key="london").exists()


@pytest.mark.django_db
def test_loader_returns_stable_counts(cities500_file):
    first = load_geonames(cities500_file)
    assert first == {
        "canadian_places": 3,
        "non_us_cities": 7,
        "aliases": 24,
        "skipped": 3,
    }
    assert NonUsCity.objects.count() == 7
    assert NonUsCityAlias.objects.count() == 14
    assert PlaceAlias.objects.filter(source=PlaceAlias.Source.GEONAMES).count() == 10

    second = load_geonames(cities500_file)
    assert second == first
    assert NonUsCity.objects.count() == 7
    assert NonUsCityAlias.objects.count() == 14


@pytest.mark.django_db
def test_canadian_place_gets_aliases_and_lookup_row(cities500_file):
    load_geonames(cities500_file)

    ottawa = Place.objects.get(canonical_key="ca:city:6094817")
    assert alias_keys(ottawa) == {
        "ottawa",
        "ottawaon",
        "ottawaontario",
        "outaouais",
    }
    assert set(ottawa.aliases.values_list("source", flat=True)) == {"geonames"}

    kingston = Place.objects.get(canonical_key="ca:city:5992500")
    assert "kingstonontario" in alias_keys(kingston)

    lookup = NonUsCity.objects.get(geoname_id=6094817)
    assert lookup.country == "CA"
    assert lookup.admin1 == "ON"
    assert lookup.population == 812129
    assert alias_keys(lookup) == {"ottawa", "outaouais"}


@pytest.mark.django_db
def test_min_population_includes_smaller_non_us_places(cities500_file):
    stats = load_geonames(cities500_file, min_population=1000)

    assert stats == {
        "canadian_places": 3,
        "non_us_cities": 8,
        "aliases": 25,
        "skipped": 2,
    }
    smallville = NonUsCity.objects.get(name="Smallville")
    assert smallville.population == 9000
    assert alias_keys(smallville) == {"smallville"}


@pytest.mark.django_db
def test_alternate_names_and_accents_are_normalized(cities500_file):
    load_geonames(cities500_file)

    london = NonUsCity.objects.get(geoname_id=2643743)
    assert alias_keys(london) == {"london", "londres", "londra"}

    belfast = NonUsCity.objects.get(geoname_id=2655984)
    assert "bealfeirste" in alias_keys(belfast)


@pytest.mark.django_db
def test_us_rows_fill_county_without_creating_places(cities500_file, us_places):
    place_count = Place.objects.count()

    stats = load_geonames(cities500_file)

    # Only the three Canadian rows create places; US rows are county backfill.
    assert Place.objects.count() == place_count + stats["canadian_places"]
    assert Place.objects.filter(country="US").count() == place_count
    assert not NonUsCity.objects.filter(
        geoname_id__in=[4829449, 4061300, 4049979, 4250542, 4061301]
    ).exists()
    assert stats["non_us_cities"] == 7

    fairview = Place.objects.get(pk=us_places["fairview"].pk)
    assert fairview.county == us_places["henry"]

    birmingham = Place.objects.get(pk=us_places["birmingham"].pk)
    assert birmingham.county == us_places["jefferson"]

    # An existing county is never overwritten.
    abbeville = Place.objects.get(pk=us_places["abbeville"].pk)
    assert abbeville.county == us_places["alameda"]

    # Two same-named places make the match ambiguous; both stay null.
    springfield_city = Place.objects.get(pk=us_places["springfield_city"].pk)
    springfield_village = Place.objects.get(pk=us_places["springfield_village"].pk)
    assert springfield_city.county is None
    assert springfield_village.county is None

    # No loaded county matches the admin2 value.
    nowhere = Place.objects.get(pk=us_places["nowhere"].pk)
    assert nowhere.county is None


@pytest.mark.django_db
def test_command_loads_source_dir(cities500_dir):
    stdout = StringIO()

    call_command(
        "load_watch_geonames",
        "--source-dir",
        str(cities500_dir),
        stdout=stdout,
    )

    assert Place.objects.filter(place_type=Place.PlaceType.CANADIAN_CITY).count() == 3
    assert NonUsCity.objects.count() == 7
    assert "canadian_places=3" in stdout.getvalue()
    assert "non_us_cities=7" in stdout.getvalue()


@pytest.mark.django_db
def test_command_passes_min_population(cities500_dir):
    stdout = StringIO()

    call_command(
        "load_watch_geonames",
        "--source-dir",
        str(cities500_dir),
        "--min-population",
        "1000",
        stdout=stdout,
    )

    assert NonUsCity.objects.filter(name="Smallville").exists()
    assert "non_us_cities=8" in stdout.getvalue()


@pytest.mark.django_db
def test_command_downloads_when_requested(cities500_file, monkeypatch):
    calls = []

    def fake_download(directory):
        calls.append(Path(directory))
        return cities500_file

    monkeypatch.setattr(
        "watch.management.commands.load_watch_geonames.download_geonames",
        fake_download,
    )
    stdout = StringIO()

    call_command("load_watch_geonames", "--download", stdout=stdout)

    assert calls
    assert NonUsCity.objects.count() == 7
    assert "non_us_cities=7" in stdout.getvalue()


def test_command_rejects_download_with_source_dir(cities500_dir):
    with pytest.raises(
        CommandError, match="--download cannot be combined with --source-dir"
    ):
        call_command(
            "load_watch_geonames",
            "--download",
            "--source-dir",
            str(cities500_dir),
        )


def test_command_requires_cities_file(tmp_path):
    with pytest.raises(CommandError, match="cities500.txt"):
        call_command("load_watch_geonames", "--source-dir", str(tmp_path))
