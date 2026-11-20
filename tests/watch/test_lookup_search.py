from types import SimpleNamespace

import pytest

from tests.factories import PlaceFactory, PlaceWithMuniFactory
from watch.lookup import search_place


@pytest.mark.django_db
def test_search_place_falls_back_to_all_dates(monkeypatch):
    place = PlaceWithMuniFactory()
    calls = []

    def fake_search(**kwargs):
        calls.append(kwargs)
        return ([], 0) if kwargs["date_from"] else ([{"id": "p1"}], 1)

    monkeypatch.setattr(
        "searches.search_backends.get_search_backend",
        lambda: SimpleNamespace(search_with_cache=fake_search),
    )
    assert search_place(place, "budget") == [{"id": "p1"}]
    assert len(calls) == 2
    assert calls[0]["date_from"] is not None
    assert calls[1]["date_from"] is None


@pytest.mark.django_db
def test_search_place_without_muni_returns_empty(monkeypatch):
    place = PlaceFactory(muni=None)
    monkeypatch.setattr(
        "searches.search_backends.get_search_backend",
        lambda: pytest.fail("must not search"),
    )
    assert search_place(place, "budget") == []
