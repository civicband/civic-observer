"""Tests for the uncovered-place backlog, its ranking, and place notes."""

from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from tests.factories import (
    PlaceFactory,
    PlaceWithMuniFactory,
    SubredditFactory,
    ThreadFactory,
    ThreadPlaceFactory,
)
from watch.models import PlaceNote


def _subject(place, *, subreddit=None, last_seen=None):
    """Link a new subject thread to ``place`` and return it."""
    return ThreadPlaceFactory(
        thread=ThreadFactory(
            subreddit=subreddit,
            last_seen=last_seen or timezone.now(),
        ),
        place=place,
        role="subject",
    )


@pytest.mark.django_db
def test_backlog_lists_only_uncovered_and_ranks(admin_client):
    busy = PlaceFactory(name="Busy", muni=None)
    quiet = PlaceFactory(name="Quiet", muni=None)
    for _ in range(2):
        ThreadPlaceFactory(
            thread=ThreadFactory(),
            place=busy,
            role="subject",
            confidence="high",
            source="subreddit",
        )
    ThreadPlaceFactory(
        thread=ThreadFactory(),
        place=quiet,
        role="subject",
        confidence="high",
        source="subreddit",
    )
    PlaceFactory(name="NoThreads", muni=None)
    _subject(PlaceWithMuniFactory(name="Covered"))

    response = admin_client.get(reverse("watch:backlog"))

    names = [p.name for p in response.context["places"]]
    assert names == ["Busy", "Quiet"]


@pytest.mark.django_db
def test_backlog_breaks_thread_ties_by_subreddits_then_population(admin_client):
    wide = PlaceFactory(name="Wide", muni=None, population=10)
    narrow = PlaceFactory(name="Narrow", muni=None, population=999999)
    first = SubredditFactory(name="first")
    second = SubredditFactory(name="second")
    _subject(wide, subreddit=first)
    _subject(wide, subreddit=second)
    _subject(narrow, subreddit=first)
    _subject(narrow, subreddit=first)

    response = admin_client.get(reverse("watch:backlog"))

    places = response.context["places"]
    assert [p.name for p in places] == ["Wide", "Narrow"]
    assert places[0].subject_threads == 2
    assert places[0].subject_subreddits == 2
    assert places[1].subject_threads == 2
    assert places[1].subject_subreddits == 1


@pytest.mark.django_db
def test_backlog_ranks_larger_population_first_with_nulls_last(admin_client):
    small = PlaceFactory(name="Small", muni=None, population=100)
    large = PlaceFactory(name="Large", muni=None, population=5000)
    unknown = PlaceFactory(name="Unknown", muni=None, population=None)
    for place in (small, large, unknown):
        _subject(place)

    response = admin_client.get(reverse("watch:backlog"))

    assert [p.name for p in response.context["places"]] == [
        "Large",
        "Small",
        "Unknown",
    ]


@pytest.mark.django_db
def test_backlog_exposes_counts_samples_and_notes(admin_client):
    place = PlaceFactory(name="Sampleville", muni=None)
    old = _subject(place, last_seen=timezone.now() - timedelta(days=2)).thread
    newest = _subject(place, last_seen=timezone.now()).thread
    middle = _subject(place, last_seen=timezone.now() - timedelta(days=1)).thread
    fourth = _subject(place, last_seen=timezone.now() - timedelta(days=3)).thread
    note = PlaceNote.objects.create(place=place, text="Existing note")

    response = admin_client.get(reverse("watch:backlog"))

    row = response.context["places"][0]
    assert row.subject_threads == 4
    assert [tp.thread for tp in row.sample_threads] == [newest, middle, old]
    assert fourth not in [tp.thread for tp in row.sample_threads]
    assert list(row.notes.all()) == [note]


@pytest.mark.django_db
def test_backlog_renders_ranking_table(admin_client):
    place = PlaceFactory(
        name="Sampleville",
        state="OR",
        place_type="city",
        population=4321,
        muni=None,
    )
    _subject(place)
    response = admin_client.get(reverse("watch:backlog"))
    content = response.content.decode()

    assert response.status_code == 200
    assert "Sampleville" in content
    assert "OR" in content
    assert "City" in content
    assert "4321" in content
    assert "No uncovered places yet." not in content
    assert reverse("watch:backlog-note", args=[place.pk]) in content
    assert 'name="status"' in content


@pytest.mark.django_db
def test_backlog_empty_state(admin_client):
    response = admin_client.get(reverse("watch:backlog"))
    assert "No uncovered places yet." in response.content.decode()


@pytest.mark.django_db
def test_backlog_note_is_created(admin_client):
    place = PlaceFactory(muni=None)
    response = admin_client.post(
        reverse("watch:backlog-note", args=[place.id]),
        {"text": "Worth adding", "status": "open"},
    )

    assert response.status_code in {200, 302}
    note = place.notes.get()
    assert note.text == "Worth adding"
    assert note.status == PlaceNote.Status.OPEN


@pytest.mark.django_db
def test_backlog_note_htmx_refreshes_only_that_row(admin_client):
    place = PlaceFactory(name="Refreshville", muni=None)
    _subject(place)
    response = admin_client.post(
        reverse("watch:backlog-note", args=[place.pk]),
        {"text": "Call the clerk", "status": "maybe"},
        HTTP_HX_REQUEST="true",
    )
    content = response.content.decode()

    assert response.status_code == 200
    assert [t.name for t in response.templates] == ["watch/partials/backlog_rows.html"]
    assert content.count("<tr") == 1
    assert "Refreshville" in content
    assert "Call the clerk" in content
    assert "Maybe" in content


@pytest.mark.django_db
def test_backlog_note_rejects_blank_text(admin_client):
    place = PlaceFactory(muni=None)
    response = admin_client.post(
        reverse("watch:backlog-note", args=[place.pk]),
        {"text": "", "status": "open"},
    )
    assert response.status_code == 400
    assert not place.notes.exists()


@pytest.mark.django_db
def test_backlog_note_requires_staff(client, user):
    place = PlaceFactory(muni=None)
    url = reverse("watch:backlog-note", args=[place.pk])

    assert client.post(url, {"text": "nope", "status": "open"}).status_code == 302

    client.force_login(user)
    response = client.post(url, {"text": "nope", "status": "open"})
    assert response.status_code in {302, 403}
    assert not place.notes.exists()


@pytest.mark.django_db
def test_backlog_query_count_does_not_grow_with_places(admin_client):
    for index in range(2):
        place = PlaceFactory(name=f"Place {index}", muni=None)
        _subject(place)
        PlaceNote.objects.create(place=place, text="Note")
    url = reverse("watch:backlog")

    with CaptureQueriesContext(connection) as few:
        admin_client.get(url)

    for index in range(2, 8):
        place = PlaceFactory(name=f"Place {index}", muni=None)
        _subject(place)
        PlaceNote.objects.create(place=place, text="Note")

    with CaptureQueriesContext(connection) as many:
        admin_client.get(url)

    assert len(many) == len(few)
