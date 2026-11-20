"""Tests for triage action and place-correction endpoints."""

from datetime import timedelta
from unittest.mock import Mock

import pytest
from django.urls import reverse
from django.utils import timezone

from tests.factories import (
    PlaceFactory,
    SubredditFactory,
    ThreadFactory,
    ThreadPlaceFactory,
)
from watch.lookup import build_query
from watch.models import MutedCountry, PlaceAlias, ThreadPlace
from watch.scoring import queue_queryset


def action_url(thread):
    return reverse("watch:action", args=[thread.id])


def correction_url(thread):
    return reverse("watch:place-correct", args=[thread.id])


@pytest.mark.django_db
def test_replied_action_persists(admin_client):
    thread = ThreadFactory()
    response = admin_client.post(action_url(thread), {"action": "replied"})
    thread.refresh_from_db()
    assert response.status_code == 302
    assert thread.status == "replied"


@pytest.mark.django_db
def test_dismiss_action_persists(admin_client):
    thread = ThreadFactory()
    admin_client.post(action_url(thread), {"action": "dismiss"})
    thread.refresh_from_db()
    assert thread.status == "dismissed"


@pytest.mark.django_db
def test_backlog_action_persists(admin_client):
    thread = ThreadFactory()
    admin_client.post(action_url(thread), {"action": "backlog"})
    thread.refresh_from_db()
    assert thread.status == "backlog"


@pytest.mark.django_db
def test_snooze_sets_future_timestamp(admin_client):
    thread = ThreadFactory()
    admin_client.post(action_url(thread), {"action": "snooze", "days": 3})
    thread.refresh_from_db()
    assert thread.status == "snoozed"
    assert thread.snoozed_until > timezone.now() + timedelta(days=2)
    assert thread.snoozed_until < timezone.now() + timedelta(days=4)


@pytest.mark.django_db
def test_snooze_defaults_to_seven_days(admin_client):
    thread = ThreadFactory()
    admin_client.post(action_url(thread), {"action": "snooze"})
    thread.refresh_from_db()
    assert thread.status == "snoozed"
    assert thread.snoozed_until > timezone.now() + timedelta(days=6)
    assert thread.snoozed_until < timezone.now() + timedelta(days=8)


@pytest.mark.django_db
@pytest.mark.parametrize("days", ["0", "91", "many"])
def test_snooze_rejects_days_outside_one_to_ninety(admin_client, days):
    thread = ThreadFactory()
    response = admin_client.post(action_url(thread), {"action": "snooze", "days": days})
    thread.refresh_from_db()
    assert response.status_code == 400
    assert thread.status == "new"
    assert thread.snoozed_until is None


@pytest.mark.django_db
def test_mute_subreddit_sets_flag_and_keeps_status(admin_client):
    subreddit = SubredditFactory()
    thread = ThreadFactory(subreddit=subreddit)
    admin_client.post(action_url(thread), {"action": "mute_subreddit"})
    subreddit.refresh_from_db()
    thread.refresh_from_db()
    assert subreddit.muted is True
    assert thread.status == "new"


@pytest.mark.django_db
def test_mute_subreddit_without_subreddit_is_bad_request(admin_client):
    thread = ThreadFactory(subreddit=None)
    response = admin_client.post(action_url(thread), {"action": "mute_subreddit"})
    assert response.status_code == 400


@pytest.mark.django_db
def test_mute_country_hides_thread(admin_client):
    subreddit = SubredditFactory(country="IE")
    thread = ThreadFactory(subreddit=subreddit)
    admin_client.post(action_url(thread), {"action": "mute_country"})
    assert MutedCountry.objects.filter(code="IE").exists()
    assert thread not in queue_queryset()


@pytest.mark.django_db
def test_mute_country_without_country_is_bad_request(admin_client):
    thread = ThreadFactory()
    response = admin_client.post(action_url(thread), {"action": "mute_country"})
    assert response.status_code == 400
    assert not MutedCountry.objects.exists()


@pytest.mark.django_db
def test_promote_makes_mentioned_place_a_manual_subject(admin_client):
    thread = ThreadFactory()
    place = PlaceFactory()
    ThreadPlaceFactory(thread=thread, place=place, role="mentioned", source="comment")
    admin_client.post(action_url(thread), {"action": "promote", "place_id": place.id})
    thread_place = ThreadPlace.objects.get(thread=thread, place=place)
    assert thread_place.role == "subject"
    assert thread_place.confidence == "high"
    assert thread_place.source == "manual"


@pytest.mark.django_db
def test_promote_without_place_is_bad_request(admin_client):
    thread = ThreadFactory()
    response = admin_client.post(action_url(thread), {"action": "promote"})
    assert response.status_code == 400


@pytest.mark.django_db
def test_unknown_action_is_bad_request(admin_client):
    thread = ThreadFactory()
    response = admin_client.post(action_url(thread), {"action": "explode"})
    thread.refresh_from_db()
    assert response.status_code == 400
    assert thread.status == "new"


@pytest.mark.django_db
def test_action_rejects_get(admin_client):
    thread = ThreadFactory()
    assert admin_client.get(action_url(thread)).status_code == 405


@pytest.mark.django_db
def test_anonymous_cannot_post_action(client):
    thread = ThreadFactory()
    assert client.post(action_url(thread), {"action": "replied"}).status_code == 302
    thread.refresh_from_db()
    assert thread.status == "new"


@pytest.mark.django_db
def test_action_htmx_returns_the_next_thread_card(admin_client):
    first = ThreadFactory(title="First thread", last_seen=timezone.now())
    ThreadFactory(
        title="Second thread", last_seen=timezone.now() - timedelta(minutes=1)
    )

    response = admin_client.post(
        action_url(first),
        {"action": "replied"},
        HTTP_HX_REQUEST="true",
    )

    content = response.content.decode()
    assert response.status_code == 200
    assert [t.name for t in response.templates] == ["watch/partials/triage_card.html"]
    assert "Second thread" in content
    assert "First thread" not in content
    assert 'id="triage-card"' not in content


@pytest.mark.django_db
def test_action_htmx_with_empty_queue_returns_empty_state(admin_client):
    thread = ThreadFactory()
    response = admin_client.post(
        action_url(thread), {"action": "dismiss"}, HTTP_HX_REQUEST="true"
    )
    assert response.status_code == 200
    assert b"No threads to triage." in response.content


@pytest.mark.django_db
def test_edit_query_sets_override_and_refreshes_hits(admin_client, monkeypatch):
    thread = ThreadFactory()
    place = PlaceFactory()
    ThreadPlaceFactory(thread=thread, place=place, role="subject")
    refresh = Mock(return_value=0)
    monkeypatch.setattr("watch.views.refresh_thread_hits", refresh)

    response = admin_client.post(
        action_url(thread),
        {"action": "edit_query", "place_id": place.id, "query": "budget 2026"},
    )

    thread_place = ThreadPlace.objects.get(thread=thread, place=place)
    assert response.status_code == 302
    assert thread_place.query_override == "budget 2026"
    assert build_query(thread, place=place) == "budget 2026"
    refresh.assert_called_once_with(thread)


@pytest.mark.django_db
def test_edit_query_uses_the_only_subject_place_when_omitted(admin_client, monkeypatch):
    thread = ThreadFactory()
    place = PlaceFactory()
    ThreadPlaceFactory(thread=thread, place=place, role="subject")
    monkeypatch.setattr("watch.views.refresh_thread_hits", Mock(return_value=0))

    response = admin_client.post(
        action_url(thread), {"action": "edit_query", "query": "parks"}
    )

    assert response.status_code == 302
    thread_place = ThreadPlace.objects.get(thread=thread, place=place)
    assert thread_place.query_override == "parks"


@pytest.mark.django_db
def test_edit_query_without_place_when_multiple_subjects_is_bad_request(
    admin_client, monkeypatch
):
    thread = ThreadFactory()
    first = PlaceFactory()
    second = PlaceFactory()
    ThreadPlaceFactory(thread=thread, place=first, role="subject")
    ThreadPlaceFactory(thread=thread, place=second, role="subject")
    refresh = Mock(return_value=0)
    monkeypatch.setattr("watch.views.refresh_thread_hits", refresh)

    response = admin_client.post(
        action_url(thread), {"action": "edit_query", "query": "parks"}
    )

    assert response.status_code == 400
    assert not ThreadPlace.objects.filter(query_override="parks").exists()
    refresh.assert_not_called()


@pytest.mark.django_db
def test_edit_query_rejects_unknown_thread_place(admin_client, monkeypatch):
    thread = ThreadFactory()
    other_thread = ThreadFactory()
    place = PlaceFactory()
    ThreadPlaceFactory(thread=other_thread, place=place, role="subject")
    refresh = Mock(return_value=0)
    monkeypatch.setattr("watch.views.refresh_thread_hits", refresh)

    response = admin_client.post(
        action_url(thread),
        {"action": "edit_query", "place_id": place.id, "query": "parks"},
    )

    assert response.status_code == 400
    refresh.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize("query", ["", "   "])
def test_edit_query_requires_a_query(admin_client, monkeypatch, query):
    thread = ThreadFactory()
    place = PlaceFactory()
    ThreadPlaceFactory(thread=thread, place=place, role="subject")
    refresh = Mock(return_value=0)
    monkeypatch.setattr("watch.views.refresh_thread_hits", refresh)

    response = admin_client.post(
        action_url(thread),
        {"action": "edit_query", "place_id": place.id, "query": query},
    )

    assert response.status_code == 400
    refresh.assert_not_called()


@pytest.mark.django_db
def test_correction_sets_manual_thread_place(admin_client):
    thread = ThreadFactory()
    place = PlaceFactory()
    response = admin_client.post(
        correction_url(thread),
        {"place_id": place.id, "role": "subject", "matched_text": "Chico"},
    )
    assert response.status_code == 302
    thread_place = ThreadPlace.objects.get(thread=thread, place=place)
    assert thread_place.source == "manual"
    assert thread_place.role == "subject"
    assert thread_place.confidence == "high"


@pytest.mark.django_db
def test_correction_defaults_role_to_subject(admin_client):
    thread = ThreadFactory()
    place = PlaceFactory()
    admin_client.post(correction_url(thread), {"place_id": place.id})
    thread_place = ThreadPlace.objects.get(thread=thread, place=place)
    assert thread_place.role == "subject"


@pytest.mark.django_db
def test_correction_creates_manual_nickname_alias(admin_client):
    thread = ThreadFactory()
    place = PlaceFactory()
    admin_client.post(
        correction_url(thread),
        {"place_id": place.id, "role": "subject", "matched_text": "Chico"},
    )
    alias = PlaceAlias.objects.get(place=place, key="chico")
    assert alias.kind == "nickname"
    assert alias.source == "manual"


@pytest.mark.django_db
def test_correction_updates_non_manual_subreddit_label(admin_client):
    old_place = PlaceFactory(name="Oldville")
    new_place = PlaceFactory(name="Newville")
    subreddit = SubredditFactory(source="gazetteer", place=old_place, country="US")
    thread = ThreadFactory(subreddit=subreddit)

    admin_client.post(
        correction_url(thread),
        {"place_id": new_place.id, "role": "subject"},
    )

    subreddit.refresh_from_db()
    assert subreddit.place == new_place
    assert subreddit.source == "manual"
    assert subreddit.reviewed_at is not None


@pytest.mark.django_db
def test_correction_leaves_manual_subreddit_label_alone(admin_client):
    old_place = PlaceFactory(name="Oldville")
    new_place = PlaceFactory(name="Newville")
    reviewed_at = timezone.now() - timedelta(days=2)
    subreddit = SubredditFactory(
        source="manual", place=old_place, reviewed_at=reviewed_at
    )
    thread = ThreadFactory(subreddit=subreddit)

    admin_client.post(
        correction_url(thread),
        {"place_id": new_place.id, "role": "subject"},
    )

    subreddit.refresh_from_db()
    assert subreddit.place == old_place
    assert subreddit.reviewed_at == reviewed_at


@pytest.mark.django_db
def test_correction_requires_place(admin_client):
    thread = ThreadFactory()
    response = admin_client.post(correction_url(thread), {"role": "subject"})
    assert response.status_code == 400
    assert not ThreadPlace.objects.filter(thread=thread).exists()


@pytest.mark.django_db
def test_correction_rejects_get(admin_client):
    thread = ThreadFactory()
    assert admin_client.get(correction_url(thread)).status_code == 405


@pytest.mark.django_db
def test_correction_htmx_returns_the_next_thread_card(admin_client):
    first = ThreadFactory(title="First thread", last_seen=timezone.now())
    ThreadFactory(
        title="Second thread", last_seen=timezone.now() - timedelta(minutes=1)
    )
    place = PlaceFactory()

    response = admin_client.post(
        correction_url(first),
        {"place_id": place.id, "role": "subject"},
        HTTP_HX_REQUEST="true",
    )

    content = response.content.decode()
    assert response.status_code == 200
    assert [t.name for t in response.templates] == ["watch/partials/triage_card.html"]
    assert "Second thread" in content
    assert 'id="triage-card"' not in content


@pytest.mark.django_db
def test_triage_card_wires_every_action_to_the_swap_target(admin_client):
    thread = ThreadFactory()
    subject = PlaceFactory()
    mentioned = PlaceFactory()
    ThreadPlaceFactory(thread=thread, place=subject, role="subject")
    ThreadPlaceFactory(thread=thread, place=mentioned, role="mentioned")

    content = admin_client.get(
        reverse("watch:triage"), {"thread": thread.pk}
    ).content.decode()

    actions = [
        "replied",
        "dismiss",
        "snooze",
        "backlog",
        "promote",
        "mute_subreddit",
        "mute_country",
        "edit_query",
    ]
    for action in actions:
        assert f'data-action="{action}"' in content
        assert f'name="action" value="{action}"' in content

    assert content.count(f'hx-post="{action_url(thread)}"') >= len(actions)
    assert content.count('hx-target="#triage-card"') >= len(actions)
    assert content.count("csrfmiddlewaretoken") >= len(actions)


@pytest.mark.django_db
def test_triage_card_wires_place_correction_form(admin_client):
    thread = ThreadFactory()
    place = PlaceFactory()
    ThreadPlaceFactory(thread=thread, place=place, role="subject")

    content = admin_client.get(
        reverse("watch:triage"), {"thread": thread.pk}
    ).content.decode()

    assert f'hx-post="{correction_url(thread)}"' in content
    assert 'hx-target="#triage-card"' in content
    assert f'name="place_id" value="{place.id}"' in content
    assert 'name="matched_text"' in content
    assert 'name="role"' in content
