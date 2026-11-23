"""Tests for the split-pane triage additions: place typeahead, no-place
action, filtered list selection, and the meeting-hit states."""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from tests.factories import (
    PlaceFactory,
    PlaceWithMuniFactory,
    SubredditFactory,
    ThreadFactory,
    ThreadPlaceFactory,
)
from watch.models import ThreadLabel, ThreadPlace


def search_url():
    return reverse("watch:place-search")


def action_url(thread):
    return reverse("watch:action", args=[thread.id])


@pytest.mark.django_db
def test_place_search_requires_staff(authenticated_client):
    response = authenticated_client.get(search_url(), {"q": "chi"})
    assert response.status_code in {302, 403}


@pytest.mark.django_db
def test_place_search_matches_by_name(admin_client):
    PlaceFactory(name="Chico", state="CA")
    PlaceFactory(name="Redding", state="CA")

    response = admin_client.get(search_url(), {"q": "chi"})

    content = response.content.decode()
    assert response.status_code == 200
    assert "Chico" in content
    assert "Redding" not in content


@pytest.mark.django_db
def test_place_search_matches_by_alias(admin_client):
    place = PlaceFactory(name="Fountain Valley")
    from watch.models import PlaceAlias

    PlaceAlias.objects.create(
        place=place, key="fountainvalley", kind="nickname", source="manual"
    )

    response = admin_client.get(search_url(), {"q": "fountainvalley"})

    assert "Fountain Valley" in response.content.decode()


@pytest.mark.django_db
def test_place_search_empty_query_returns_no_results(admin_client):
    PlaceFactory(name="Chico")
    response = admin_client.get(search_url(), {"q": ""})
    assert response.status_code == 200
    assert "Chico" not in response.content.decode()


@pytest.mark.django_db
def test_place_search_caps_results(admin_client):
    for i in range(12):
        PlaceFactory(name=f"Springfield {i}")

    response = admin_client.get(search_url(), {"q": "Springfield"})

    assert response.content.decode().count("data-place-result") == 8


@pytest.mark.django_db
def test_no_place_action_records_a_null_place_label(admin_client):
    thread = ThreadFactory()

    response = admin_client.post(action_url(thread), {"action": "no_place"})

    assert response.status_code == 302
    label = ThreadLabel.objects.get(thread=thread)
    assert label.place is None


@pytest.mark.django_db
def test_triage_list_selects_requested_thread(admin_client):
    newest = ThreadFactory(last_seen=timezone.now())
    older = ThreadFactory(last_seen=timezone.now() - timedelta(minutes=1))

    response = admin_client.get(reverse("watch:triage"), {"thread": older.pk})

    assert response.context["thread"] == older
    assert response.context["previous_thread"] == newest


@pytest.mark.django_db
def test_triage_filter_narrows_the_list_and_selection(admin_client):
    covered = ThreadFactory(last_seen=timezone.now())
    ThreadPlaceFactory(thread=covered, place=PlaceWithMuniFactory(), role="subject")
    ThreadFactory(last_seen=timezone.now() - timedelta(minutes=1))

    response = admin_client.get(reverse("watch:triage"), {"covered": "yes"})

    assert response.context["queue_count"] == 1
    assert response.context["thread"] == covered


@pytest.mark.django_db
def test_triage_meeting_hits_shows_uncovered_state(admin_client):
    thread = ThreadFactory()
    ThreadPlace.objects.create(
        thread=thread,
        place=PlaceFactory(muni=None),
        role="subject",
        confidence="high",
        source="subreddit",
    )

    content = admin_client.get(
        reverse("watch:triage"), {"thread": thread.pk}
    ).content.decode()

    assert "Uncovered place" in content


@pytest.mark.django_db
def test_triage_meeting_hits_shows_no_hits_state(admin_client):
    thread = ThreadFactory()
    ThreadPlace.objects.create(
        thread=thread,
        place=PlaceWithMuniFactory(),
        role="subject",
        confidence="high",
        source="subreddit",
    )

    content = admin_client.get(
        reverse("watch:triage"), {"thread": thread.pk}
    ).content.decode()

    assert "No meeting hits yet." in content


@pytest.mark.django_db
def test_triage_list_filters_by_subreddit_kind(admin_client):
    place_sub = SubredditFactory(kind="place")
    place_thread = ThreadFactory(subreddit=place_sub)
    ThreadFactory(subreddit=SubredditFactory(kind="general"))

    response = admin_client.get(reverse("watch:triage"), {"kind": "place"})

    assert response.context["queue_count"] == 1
    assert response.context["thread"] == place_thread


@pytest.mark.django_db
def test_action_form_keeps_list_filters(admin_client):
    ThreadFactory(title="Keep this one")

    content = admin_client.get(
        reverse("watch:triage"), {"covered": "no", "sort": "oldest"}
    ).content.decode()

    # Action forms must carry the active filters so the OOB list refresh and
    # the next-thread advance stay inside the filtered queue.
    assert "watch:action" not in content or "?covered=no&sort=oldest" in content
    assert "covered=no&sort=oldest" in content


@pytest.mark.django_db
def test_action_after_post_keeps_filters_in_list(admin_client):
    ThreadFactory(title="Filtered in", last_seen=timezone.now())
    ThreadFactory(title="Filtered out", last_seen=timezone.now())
    excluded = ThreadFactory(title="Already dismissed", last_seen=timezone.now())
    excluded.status = "dismissed"
    excluded.save(update_fields=["status"])

    response = admin_client.post(
        action_url(excluded),
        {"action": "dismiss"},
        HTTP_HX_REQUEST="true",
        QUERY_STRING="covered=no",
    )

    body = response.content.decode()
    assert "Filtered in" in body
    assert "Filtered out" in body
    # The dismissed thread stays out of the refreshed list, matching the filter.
    assert "Already dismissed" not in body
