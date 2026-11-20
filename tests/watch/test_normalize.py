import json

import httpx
import pytest
from django.conf import settings

from watch.normalize import (
    STATE_FIPS,
    clean_name,
    download,
    ensure_data_dir,
    normalize_key,
    state_from_fips,
    state_name,
)


def test_normalize_key_strips_punctuation_and_case():
    assert normalize_key("La Crosse, WI") == "lacrossewi"
    assert normalize_key("Fountain–Colorado") == "fountaincolorado"


def test_state_helpers():
    assert state_from_fips("06") == "CA"
    assert state_from_fips("6") == "CA"
    assert state_from_fips(" 6 ") == "CA"
    assert state_name("CA") == "California"


def test_state_name_returns_plain_strings():
    name = state_name("CA")
    assert isinstance(name, str)
    assert json.dumps({"s": name}) == '{"s": "California"}'


def test_state_fips_table_shape_and_sentinels():
    assert len(STATE_FIPS) == 52
    assert state_from_fips("11") == "DC"
    assert state_from_fips("72") == "PR"
    assert state_from_fips("55") == "WI"


def test_clean_name_strips_unambiguous_legal_suffixes():
    assert clean_name("Cecil County") == "Cecil"
    assert clean_name("East Windsor township") == "East Windsor"
    assert clean_name("Springfield  school district") == "Springfield"
    assert clean_name("Abbeville CDP") == "Abbeville"


def test_clean_name_preserves_ambiguous_suffixes_by_default():
    assert clean_name("Kansas City") == "Kansas City"
    assert clean_name("Carson City") == "Carson City"
    assert clean_name("Abbeville city") == "Abbeville city"
    assert clean_name("East Windsor town") == "East Windsor town"


def test_clean_name_strips_explicit_suffix():
    assert clean_name("Kansas City city", suffix="city") == "Kansas City"
    assert clean_name("Abbeville CITY", suffix="city") == "Abbeville"
    assert clean_name("East Windsor town", suffix="town") == "East Windsor"
    assert clean_name("Cecil County", suffix="county") == "Cecil"


def test_clean_name_with_unmatched_explicit_suffix_is_unchanged():
    assert clean_name("Kansas City", suffix="village") == "Kansas City"


def test_state_from_fips_unknown_raises_key_error():
    with pytest.raises(KeyError):
        state_from_fips("99")


def test_state_name_provinces():
    assert state_name("ON") == "Ontario"
    assert state_name("BC") == "British Columbia"
    assert state_name("NL") == "Newfoundland and Labrador"


def test_state_name_includes_dc_and_pr():
    assert state_name("DC") == "District of Columbia"
    assert state_name("PR") == "Puerto Rico"


def test_state_name_unknown_raises_key_error():
    with pytest.raises(KeyError):
        state_name("ZZ")


def test_normalize_key_keeps_digits():
    assert normalize_key("District 5") == "district5"


def test_normalize_key_strips_combining_marks():
    assert normalize_key("Montréal") == "montreal"


def test_clean_name_collapses_whitespace():
    assert clean_name("  East   Windsor  ") == "East Windsor"
    assert clean_name("Springfield  school district") == "Springfield"


def test_ensure_data_dir_resolves_relative_paths_against_base_dir(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(settings, "BASE_DIR", tmp_path)
    monkeypatch.setattr(settings, "WATCH_DATA_DIR", "cache/watch")

    path = ensure_data_dir()

    assert path == tmp_path / "cache" / "watch"
    assert path.is_dir()


def test_ensure_data_dir_keeps_absolute_paths(monkeypatch, tmp_path):
    target = tmp_path / "absolute" / "watch"
    monkeypatch.setattr(settings, "WATCH_DATA_DIR", str(target))

    assert ensure_data_dir() == target
    assert target.is_dir()


def test_download_reuses_existing_file(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "WATCH_DATA_DIR", str(tmp_path))
    existing = tmp_path / "places.csv"
    existing.write_text("cached")

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("should not fetch when a cached file exists")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert (
        download("https://example.com/places.csv", "places.csv", client=client)
        == existing
    )
    assert existing.read_text() == "cached"


def test_download_fetches_missing_file(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "WATCH_DATA_DIR", str(tmp_path))
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(200, content=b"geoid,name\n1,Example\n")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    path = download("https://example.com/places.csv", "places.csv", client=client)

    assert requested == ["https://example.com/places.csv"]
    assert path == tmp_path / "places.csv"
    assert path.read_text() == "geoid,name\n1,Example\n"


def test_download_replaces_empty_file(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "WATCH_DATA_DIR", str(tmp_path))
    (tmp_path / "places.csv").write_text("")

    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=b"data")
        )
    )
    path = download("https://example.com/places.csv", "places.csv", client=client)

    assert path.read_text() == "data"


def test_download_supports_nested_filenames(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "WATCH_DATA_DIR", str(tmp_path))
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=b"hi")
        )
    )

    path = download(
        "https://example.com/gazetteer/places.csv",
        "gazetteer/places.csv",
        client=client,
    )

    assert path == tmp_path / "gazetteer" / "places.csv"
    assert path.read_text() == "hi"


def test_download_raises_for_error_status(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "WATCH_DATA_DIR", str(tmp_path))

    client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(404))
    )
    with pytest.raises(httpx.HTTPStatusError):
        download("https://example.com/missing.csv", "missing.csv", client=client)
    assert not (tmp_path / "missing.csv").exists()


def test_download_stops_cleanly_when_body_stream_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "WATCH_DATA_DIR", str(tmp_path))
    stale = tmp_path / "stale.part"
    stale.write_text("leave me alone")

    class BrokenStream(httpx.SyncByteStream):
        def __iter__(self):
            yield b"partial"
            raise httpx.ReadError("connection dropped")

    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, stream=BrokenStream())
        )
    )

    with pytest.raises(httpx.ReadError):
        download("https://example.com/broken.csv", "broken.csv", client=client)

    assert not (tmp_path / "broken.csv").exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["stale.part"]
    assert stale.read_text() == "leave me alone"


def test_download_cache_hit_does_not_construct_client(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "WATCH_DATA_DIR", str(tmp_path))
    existing = tmp_path / "cached.csv"
    existing.write_text("cached")

    def factory(**kwargs: object) -> httpx.Client:
        raise AssertionError("cache hit should not construct an HTTP client")

    monkeypatch.setattr("watch.normalize.httpx.Client", factory)

    assert download("https://example.com/cached.csv", "cached.csv") == existing


def test_download_creates_and_closes_default_client(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "WATCH_DATA_DIR", str(tmp_path))
    real_client = httpx.Client
    created: list[tuple[httpx.Client, dict[str, object]]] = []

    def factory(**kwargs: object) -> httpx.Client:
        transport = httpx.MockTransport(
            lambda request: httpx.Response(201, content=b"fresh")
        )
        client = real_client(transport=transport)
        created.append((client, kwargs))
        return client

    monkeypatch.setattr("watch.normalize.httpx.Client", factory)
    path = download("https://example.com/fresh.csv", "fresh.csv")

    assert path.read_text() == "fresh"
    client, kwargs = created[0]
    assert kwargs["follow_redirects"] is True
    assert client.is_closed
