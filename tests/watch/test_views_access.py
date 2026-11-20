"""Access control and context tests for the staff-only triage pages."""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from tests.factories import (
    MeetingHitFactory,
    MentionFactory,
    PlaceFactory,
    PlaceWithMuniFactory,
    ThreadFactory,
    ThreadPlaceFactory,
)


@pytest.mark.django_db
def test_anonymous_is_redirected(client):
    assert client.get(reverse("watch:triage")).status_code == 302
    assert client.get(reverse("watch:backlog")).status_code == 302


@pytest.mark.django_db
def test_non_staff_is_rejected(client, user):
    client.force_login(user)
    assert client.get(reverse("watch:triage")).status_code in {302, 403}
    assert client.get(reverse("watch:backlog")).status_code in {302, 403}


@pytest.mark.django_db
def test_staff_sees_triage(admin_client):
    response = admin_client.get(reverse("watch:triage"))
    assert response.status_code == 200
    assert b"Triage" in response.content


@pytest.mark.django_db
def test_staff_sees_backlog(admin_client):
    response = admin_client.get(reverse("watch:backlog"))
    assert response.status_code == 200
    assert b"Backlog" in response.content


@pytest.mark.django_db
def test_triage_htmx_returns_only_the_card(admin_client):
    ThreadFactory()
    response = admin_client.get(reverse("watch:triage"), HTTP_HX_REQUEST="true")
    assert response.status_code == 200
    assert [t.name for t in response.templates] == ["watch/partials/triage_card.html"]
    assert b"Triage" not in response.content


@pytest.mark.django_db
def test_backlog_htmx_returns_only_the_rows(admin_client):
    place = PlaceFactory(muni=None)
    ThreadPlaceFactory(thread=ThreadFactory(), place=place, role="subject")
    response = admin_client.get(reverse("watch:backlog"), HTTP_HX_REQUEST="true")
    assert response.status_code == 200
    assert [t.name for t in response.templates] == ["watch/partials/backlog_rows.html"]
    assert b"Backlog" not in response.content


@pytest.mark.django_db
def test_triage_defaults_to_queue_head_with_navigation(admin_client):
    newest = ThreadFactory(last_seen=timezone.now())
    middle = ThreadFactory(last_seen=timezone.now() - timedelta(minutes=1))
    oldest = ThreadFactory(last_seen=timezone.now() - timedelta(minutes=2))

    response = admin_client.get(reverse("watch:triage"))

    assert response.context["thread"] == newest
    assert response.context["queue_count"] == 3
    assert response.context["next_thread"] == middle
    assert response.context["previous_thread"] is None

    response = admin_client.get(reverse("watch:triage"), {"thread": middle.pk})

    assert response.context["thread"] == middle
    assert response.context["previous_thread"] == newest
    assert response.context["next_thread"] == oldest


@pytest.mark.django_db
def test_triage_exposes_places_and_hits_for_the_thread(admin_client):
    thread = ThreadFactory()
    subject_place = PlaceWithMuniFactory()
    mentioned_place = PlaceFactory(muni=None)
    subject = ThreadPlaceFactory(thread=thread, place=subject_place, role="subject")
    mentioned = ThreadPlaceFactory(
        thread=thread, place=mentioned_place, role="mentioned"
    )
    hit = MeetingHitFactory(thread=thread, place=subject_place)
    MentionFactory(thread=thread, snippet_text="Is this on the agenda?")
    other = ThreadFactory()

    response = admin_client.get(reverse("watch:triage"), {"thread": thread.pk})

    assert list(response.context["subject_places"]) == [subject]
    assert list(response.context["mentioned_places"]) == [mentioned]
    assert response.context["meeting_hits_by_place"][subject_place.pk] == [hit]
    assert response.context["meeting_hits_by_place"].get(other.pk) is None


@pytest.mark.django_db
def test_triage_unknown_or_malformed_thread_is_404(admin_client):
    assert (
        admin_client.get(reverse("watch:triage"), {"thread": "not-a-uuid"}).status_code
        == 404
    )
    assert (
        admin_client.get(
            reverse("watch:triage"),
            {"thread": "00000000-0000-0000-0000-000000000000"},
        ).status_code
        == 404
    )


@pytest.mark.django_db
def test_backlog_lists_uncovered_places_and_counts_threads(admin_client):
    busy = PlaceFactory(name="Busy", muni=None)
    quiet = PlaceFactory(name="Quiet", muni=None)
    ThreadPlaceFactory(thread=ThreadFactory(), place=busy, role="subject")
    ThreadPlaceFactory(thread=ThreadFactory(), place=busy, role="subject")
    ThreadPlaceFactory(thread=ThreadFactory(), place=quiet, role="subject")
    ThreadPlaceFactory(
        thread=ThreadFactory(), place=PlaceWithMuniFactory(), role="subject"
    )

    response = admin_client.get(reverse("watch:backlog"))

    assert [place.name for place in response.context["places"]] == ["Busy", "Quiet"]
    assert response.context["total_threads"] == 3
