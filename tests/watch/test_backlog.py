"""Tests for the uncovered-place backlog, its ranking, and place notes."""

import uuid
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
from watch.models import MutedCountry, PlaceNote
from watch.scoring import BACKLOG_DAYS


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
def test_backlog_falls_back_to_name_for_fully_tied_places(admin_client):
    _subject(PlaceFactory(name="Beta", muni=None, population=500))
    _subject(PlaceFactory(name="Alpha", muni=None, population=500))

    response = admin_client.get(reverse("watch:backlog"))

    assert [p.name for p in response.context["places"]] == ["Alpha", "Beta"]


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
def test_backlog_samples_skip_threads_outside_the_activity_window(admin_client):
    place = PlaceFactory(muni=None)
    recent = _subject(place)
    stale = _subject(place, last_seen=timezone.now() - timedelta(days=BACKLOG_DAYS + 1))

    response = admin_client.get(reverse("watch:backlog"))

    row = response.context["places"][0]
    assert row.subject_threads == 1
    assert [tp.thread for tp in row.sample_threads] == [recent.thread]
    assert stale.thread not in [tp.thread for tp in row.sample_threads]


@pytest.mark.django_db
def test_backlog_samples_skip_muted_subreddits(admin_client):
    place = PlaceFactory(muni=None)
    allowed = _subject(place, subreddit=SubredditFactory(name="allowed"))
    _subject(place, subreddit=SubredditFactory(name="muted", muted=True))

    response = admin_client.get(reverse("watch:backlog"))

    row = response.context["places"][0]
    assert row.subject_threads == 1
    assert [tp.thread for tp in row.sample_threads] == [allowed.thread]


@pytest.mark.django_db
def test_backlog_samples_apply_country_mute_precedence(admin_client):
    place = PlaceFactory(muni=None)
    MutedCountry.objects.create(code="US")
    allowed = _subject(place, subreddit=SubredditFactory(name="allowed", country="CA"))
    muted = _subject(place, subreddit=SubredditFactory(name="muted", country="US"))

    response = admin_client.get(reverse("watch:backlog"))

    row = response.context["places"][0]
    assert row.subject_threads == 1
    assert row.subject_subreddits == 1
    assert [tp.thread for tp in row.sample_threads] == [allowed.thread]
    assert muted.thread not in [tp.thread for tp in row.sample_threads]


@pytest.mark.django_db
def test_backlog_orders_notes_newest_first(admin_client):
    place = PlaceFactory(muni=None)
    _subject(place)
    older = PlaceNote.objects.create(place=place, text="Older")
    PlaceNote.objects.filter(pk=older.pk).update(
        created=timezone.now() - timedelta(days=1)
    )
    newer = PlaceNote.objects.create(place=place, text="Newer")

    response = admin_client.get(reverse("watch:backlog"))

    assert list(response.context["places"][0].notes.all()) == [newer, older]


@pytest.mark.django_db
def test_backlog_page_renders_at_most_three_samples(admin_client):
    place = PlaceFactory(name="ManyThreads", muni=None)
    for index in range(5):
        ThreadPlaceFactory(
            thread=ThreadFactory(
                title=f"Sample thread {index}",
                last_seen=timezone.now() - timedelta(minutes=index),
            ),
            place=place,
            role="subject",
        )

    response = admin_client.get(reverse("watch:backlog"))
    content = response.content.decode()

    assert response.status_code == 200
    assert len(response.context["places"][0].sample_threads) == 3
    assert content.count("data-sample-thread=") == 3
    assert "Sample thread 0" in content


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
    content = response.content.decode()
    assert "No uncovered places match this view." in content
    assert "Try the" in content


@pytest.mark.django_db
def test_backlog_explains_purpose_and_workflow(admin_client):
    response = admin_client.get(reverse("watch:backlog"))
    content = response.content.decode()

    assert "ranked by demand" in content
    assert "How to use it:" in content
    for status in ("Open", "Maybe", "Added", "Rejected"):
        assert status in content
    # Column help: renamed Notes column and tooltips.
    assert "Decision" in content
    assert "Distinct subreddits those threads came from" in content


@pytest.mark.django_db
def test_backlog_reads_total_threads_only_for_the_full_page(admin_client):
    place = PlaceFactory(muni=None)
    _subject(place)
    url = reverse("watch:backlog")

    full = admin_client.get(url)
    partial = admin_client.get(url, HTTP_HX_REQUEST="true")

    assert "total_threads" in full.context
    assert "total_threads" not in partial.context


@pytest.mark.django_db
def test_backlog_note_is_created(admin_client):
    place = PlaceFactory(muni=None)
    response = admin_client.post(
        reverse("watch:backlog-note", args=[place.id]),
        {"text": "Worth adding", "status": "open"},
    )

    assert response.status_code == 302
    assert response.url == reverse("watch:backlog")
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
    assert "total_threads" not in response.context
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
def test_backlog_note_rejects_unknown_status(admin_client):
    place = PlaceFactory(muni=None)
    response = admin_client.post(
        reverse("watch:backlog-note", args=[place.pk]),
        {"text": "Sneaky", "status": "not-a-status"},
    )
    assert response.status_code == 400
    assert not place.notes.exists()


@pytest.mark.django_db
def test_backlog_note_rejects_covered_place(admin_client):
    place = PlaceWithMuniFactory()
    response = admin_client.post(
        reverse("watch:backlog-note", args=[place.pk]),
        {"text": "Covered", "status": "open"},
    )

    assert response.status_code == 404
    assert not place.notes.exists()


@pytest.mark.django_db
def test_backlog_note_rejects_unknown_place(admin_client):
    response = admin_client.post(
        reverse("watch:backlog-note", args=[uuid.uuid4()]),
        {"text": "Nowhere", "status": "open"},
    )
    assert response.status_code == 404


@pytest.mark.django_db
def test_backlog_note_rejects_get(admin_client):
    place = PlaceFactory(muni=None)
    response = admin_client.get(reverse("watch:backlog-note", args=[place.pk]))
    assert response.status_code == 405


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


@pytest.mark.django_db
def test_backlog_sample_threads_link_to_reddit(admin_client):
    place = PlaceFactory(name="Linkedville", muni=None)
    ThreadPlaceFactory(
        thread=ThreadFactory(key="reddit:chicoca/abc123", title="Chico thread"),
        place=place,
        role="subject",
    )

    content = admin_client.get(reverse("watch:backlog")).content.decode()

    assert "https://www.reddit.com/r/chicoca/comments/abc123/" in content
    assert 'rel="noopener noreferrer"' in content


@pytest.mark.django_db
def test_backlog_sample_threads_without_reddit_key_are_plain_text(admin_client):
    place = PlaceFactory(name="Quietville", muni=None)
    ThreadPlaceFactory(
        thread=ThreadFactory(key="not-a-url", title="Unlinked thread"),
        place=place,
        role="subject",
    )

    content = admin_client.get(reverse("watch:backlog")).content.decode()

    assert "Unlinked thread" in content
    assert 'href="not-a-url"' not in content


@pytest.mark.django_db
def test_backlog_never_lists_places_that_already_have_a_site(admin_client):
    # The backlog is defined as places with no linked site, so place names are
    # never links to a CivicBand site; only the sample threads link out.
    place = PlaceWithMuniFactory(name="Linked Place")
    _subject(place)

    assert admin_client.get(reverse("watch:backlog")).context["places"] == []
