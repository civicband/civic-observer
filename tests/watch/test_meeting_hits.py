import hashlib
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from django.utils import timezone

from tests.factories import (
    MeetingHitFactory,
    PlaceFactory,
    PlaceWithMuniFactory,
    ThreadFactory,
)
from watch.lookup import (
    meeting_hits_for,
    query_hash,
    refresh_thread_hits,
    result_to_hit,
)
from watch.models import MeetingHit, ThreadPlace


def _results(count=3):
    return [
        {
            "id": f"page-{n}",
            "rank": 1.0 - n / 10,
            "snippet": "<mark>budget</mark>",
            "meeting_name": "CityCouncil",
            "meeting_date": date(2024, 1, 15),
            "document_type": "agenda",
            "subdomain": "chico",
            "civic_band_table_name": "agendas",
            "page_image": "/_agendas/CityCouncil/2024-01-15/1.png",
        }
        for n in range(count)
    ]


def _subject(thread, place):
    return ThreadPlace.objects.create(
        thread=thread,
        place=place,
        role="subject",
        confidence="high",
        source="subreddit",
    )


def _patch_results(monkeypatch, results):
    monkeypatch.setattr(
        "searches.search_backends.get_search_backend",
        lambda: SimpleNamespace(
            search_with_cache=lambda **kwargs: (results, len(results))
        ),
    )


def test_query_hash_is_sha256_of_query():
    assert query_hash("budget parks") == hashlib.sha256(b"budget parks").hexdigest()


@pytest.mark.django_db
def test_result_to_hit_maps_citation_fields(settings):
    settings.CDN_URL = "https://cdn.example.com/"
    thread = ThreadFactory()
    place = PlaceWithMuniFactory()
    result = _results(1)[0]

    hit = result_to_hit(thread, place, "budget parks", result)

    assert hit.thread == thread
    assert hit.place == place
    assert hit.page_id == "page-0"
    assert hit.query == "budget parks"
    assert hit.query_hash == query_hash("budget parks")
    assert hit.rank == 1.0
    assert hit.snippet == "<mark>budget</mark>"
    assert hit.meeting_name == "CityCouncil"
    assert hit.meeting_date == date(2024, 1, 15)
    assert hit.document_type == "agenda"
    assert hit.civic_band_url == "https://chico.civic.band/meetings/agendas/page-0"
    assert (
        hit.thumbnail_url
        == "https://cdn.example.com/chico/_agendas/CityCouncil/2024-01-15/1.png?width=400"
    )


@pytest.mark.django_db
def test_result_to_hit_defaults_optional_fields():
    thread = ThreadFactory()
    place = PlaceWithMuniFactory()
    result = {
        "id": "page-1",
        "subdomain": "chico",
        "civic_band_table_name": "minutes",
    }

    hit = result_to_hit(thread, place, "budget", result)

    assert hit.rank is None
    assert hit.snippet == ""
    assert hit.meeting_name == ""
    assert hit.meeting_date is None
    assert hit.document_type == ""
    assert hit.thumbnail_url == ""
    assert hit.civic_band_url == "https://chico.civic.band/meetings/minutes/page-1"


@pytest.mark.django_db
def test_refresh_replaces_hits_without_duplicates(monkeypatch):
    thread = ThreadFactory()
    place = PlaceWithMuniFactory()
    _subject(thread, place)
    _patch_results(monkeypatch, _results(3))

    assert refresh_thread_hits(thread) == 3
    assert refresh_thread_hits(thread) == 3
    assert MeetingHit.objects.filter(thread=thread).count() == 3
    assert set(
        MeetingHit.objects.filter(thread=thread).values_list("page_id", flat=True)
    ) == {"page-0", "page-1", "page-2"}


@pytest.mark.django_db
def test_refresh_removes_stale_hits(monkeypatch):
    thread = ThreadFactory()
    place = PlaceWithMuniFactory()
    _subject(thread, place)
    MeetingHitFactory(thread=thread, place=place, page_id="stale")
    _patch_results(monkeypatch, _results(1))

    assert refresh_thread_hits(thread) == 1
    assert list(
        MeetingHit.objects.filter(thread=thread).values_list("page_id", flat=True)
    ) == ["page-0"]


@pytest.mark.django_db
def test_refresh_deduplicates_repeated_results(monkeypatch):
    thread = ThreadFactory()
    place = PlaceWithMuniFactory()
    _subject(thread, place)
    result = _results(1)[0]
    _patch_results(monkeypatch, [result, result])

    assert refresh_thread_hits(thread) == 1
    assert MeetingHit.objects.filter(thread=thread).count() == 1


@pytest.mark.django_db
def test_uncovered_place_is_not_searched(monkeypatch):
    thread = ThreadFactory()
    _subject(thread, PlaceFactory(muni=None))
    monkeypatch.setattr(
        "searches.search_backends.get_search_backend",
        lambda: pytest.fail("must not search"),
    )

    assert refresh_thread_hits(thread) == 0


@pytest.mark.django_db
def test_only_subject_places_are_searched(monkeypatch):
    thread = ThreadFactory()
    covered = PlaceWithMuniFactory()
    _subject(thread, covered)
    ThreadPlace.objects.create(
        thread=thread,
        place=PlaceWithMuniFactory(),
        role="mentioned",
        confidence="high",
        source="subreddit",
    )
    calls = []

    def fake_search(**kwargs):
        calls.append(kwargs)
        return _results(3), 3

    monkeypatch.setattr(
        "searches.search_backends.get_search_backend",
        lambda: SimpleNamespace(search_with_cache=fake_search),
    )

    assert refresh_thread_hits(thread) == 3
    assert len(calls) == 1
    assert calls[0]["municipalities"] == [covered.muni]
    assert set(
        MeetingHit.objects.filter(thread=thread).values_list("place_id", flat=True)
    ) == {covered.id}


@pytest.mark.django_db
def test_meeting_hits_for_prefetches_place(django_assert_num_queries):
    thread = ThreadFactory()
    place = PlaceWithMuniFactory()
    MeetingHitFactory(thread=thread, place=place)
    MeetingHitFactory(thread=ThreadFactory(), place=place)

    with django_assert_num_queries(1):
        hits = list(meeting_hits_for(thread))
        assert [hit.place.name for hit in hits] == [place.name]
    assert len(hits) == 1
    assert all(isinstance(hit, MeetingHit) for hit in hits)


@pytest.mark.django_db
def test_future_agenda_is_flagged():
    hit = MeetingHitFactory(meeting_date=timezone.localdate() + timedelta(days=3))

    assert hit.is_future is True


@pytest.mark.django_db
def test_past_and_undated_meetings_are_not_future():
    assert (
        MeetingHitFactory(
            meeting_date=timezone.localdate() - timedelta(days=1)
        ).is_future
        is False
    )
    assert MeetingHitFactory(meeting_date=None).is_future is False
