"""Tests for triage action and place-correction endpoints."""

import uuid
from datetime import timedelta
from unittest.mock import Mock

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from tests.factories import (
    PlaceFactory,
    SubredditFactory,
    ThreadFactory,
    ThreadPlaceFactory,
)
from watch.lookup import build_query
from watch.models import MutedCountry, PlaceAlias, ThreadLabel, ThreadPlace
from watch.scoring import queue_queryset, thread_country


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
def test_mute_country_leaves_thread_status_unchanged(admin_client):
    thread = ThreadFactory(subreddit=SubredditFactory(country="IE"), status="backlog")

    admin_client.post(action_url(thread), {"action": "mute_country"})

    thread.refresh_from_db()
    assert thread.status == "backlog"


@pytest.mark.django_db
def test_mute_country_rejects_the_united_states(admin_client):
    thread = ThreadFactory(subreddit=SubredditFactory(country="US"))

    response = admin_client.post(action_url(thread), {"action": "mute_country"})

    assert response.status_code == 400
    assert not MutedCountry.objects.exists()


@pytest.mark.django_db
def test_mute_country_falls_back_to_the_subreddit_place_country(admin_client):
    place = PlaceFactory(country="CA")
    subreddit = SubredditFactory(place=place)
    thread = ThreadFactory(subreddit=subreddit)

    admin_client.post(action_url(thread), {"action": "mute_country"})

    assert MutedCountry.objects.filter(code="CA").exists()
    assert thread not in queue_queryset()


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
def test_promote_rejects_a_place_from_another_thread(admin_client):
    thread = ThreadFactory()
    other_thread = ThreadFactory()
    place = PlaceFactory()
    ThreadPlaceFactory(thread=other_thread, place=place, role="mentioned")

    response = admin_client.post(
        action_url(thread), {"action": "promote", "place_id": place.id}
    )

    assert response.status_code == 400
    assert not ThreadPlace.objects.filter(thread=thread, place=place).exists()
    assert ThreadPlace.objects.get(thread=other_thread, place=place).role == "mentioned"


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
@pytest.mark.parametrize(
    ("action", "payload"),
    [
        ("replied", {}),
        ("dismiss", {}),
        ("backlog", {}),
        ("snooze", {"days": 3}),
    ],
)
def test_terminal_actions_htmx_advance_to_the_next_thread(
    admin_client, action, payload
):
    first = ThreadFactory(title="First thread", last_seen=timezone.now())
    ThreadFactory(
        title="Second thread", last_seen=timezone.now() - timedelta(minutes=1)
    )

    response = admin_client.post(
        action_url(first),
        {"action": action, **payload},
        HTTP_HX_REQUEST="true",
    )

    content = response.content.decode()
    assert response.status_code == 200
    assert response.templates[0].name == "watch/partials/triage_card.html"
    assert "Second thread" in content
    assert "First thread" not in content
    assert 'id="triage-detail"' not in content


@pytest.mark.django_db
def test_action_htmx_with_empty_queue_returns_empty_state(admin_client):
    thread = ThreadFactory()
    response = admin_client.post(
        action_url(thread), {"action": "dismiss"}, HTTP_HX_REQUEST="true"
    )
    assert response.status_code == 200
    assert b"No threads to triage." in response.content


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("action", "payload"),
    [
        ("promote", {}),
        ("edit_query", {"query": "parks"}),
        ("mute_subreddit", {}),
        ("mute_country", {}),
    ],
)
def test_stay_actions_htmx_rerender_the_current_thread(
    admin_client, monkeypatch, action, payload
):
    monkeypatch.setattr("watch.views.enqueue_enrichment", Mock(return_value="job-1"))
    subreddit = SubredditFactory(country="IE")
    first = ThreadFactory(
        title="Current thread",
        subreddit=subreddit,
        last_seen=timezone.now(),
    )
    ThreadFactory(title="Other thread", last_seen=timezone.now() - timedelta(minutes=1))
    place = PlaceFactory()
    ThreadPlaceFactory(thread=first, place=place, role="mentioned")
    data = {"action": action, **payload}
    if action in {"promote", "edit_query"}:
        data["place_id"] = place.id

    response = admin_client.post(action_url(first), data, HTTP_HX_REQUEST="true")

    content = response.content.decode()
    assert response.status_code == 200
    assert "watch/partials/triage_card.html" in [t.name for t in response.templates]
    # Scope to the detail card; the OOB list refresh may mention other threads.
    card = content.split("</article>", 1)[0]
    assert "Current thread" in card
    assert "Other thread" not in card


@pytest.mark.django_db
def test_edit_query_saves_override_and_enqueues_enrichment(admin_client, monkeypatch):
    thread = ThreadFactory()
    place = PlaceFactory()
    ThreadPlaceFactory(thread=thread, place=place, role="subject")
    enqueue = Mock(return_value="job-1")
    refresh = Mock(return_value=0)
    monkeypatch.setattr("watch.views.enqueue_enrichment", enqueue)
    monkeypatch.setattr("watch.lookup.refresh_thread_hits", refresh)

    response = admin_client.post(
        action_url(thread),
        {"action": "edit_query", "place_id": place.id, "query": "budget 2026"},
    )

    thread_place = ThreadPlace.objects.get(thread=thread, place=place)
    assert response.status_code == 302
    assert thread_place.query_override == "budget 2026"
    assert build_query(thread, place=place) == "budget 2026"
    enqueue.assert_called_once_with(str(thread.id))
    refresh.assert_not_called()


@pytest.mark.django_db
def test_edit_query_still_succeeds_when_enqueue_fails(admin_client, monkeypatch):
    thread = ThreadFactory()
    place = PlaceFactory()
    ThreadPlaceFactory(thread=thread, place=place, role="subject")
    enqueue = Mock(side_effect=RuntimeError("queue unavailable"))
    monkeypatch.setattr("watch.views.enqueue_enrichment", enqueue)

    response = admin_client.post(
        action_url(thread),
        {"action": "edit_query", "place_id": place.id, "query": "budget 2026"},
    )

    assert response.status_code == 302
    thread_place = ThreadPlace.objects.get(thread=thread, place=place)
    assert thread_place.query_override == "budget 2026"
    enqueue.assert_called_once_with(str(thread.id))


@pytest.mark.django_db
def test_edit_query_uses_the_only_subject_place_when_omitted(admin_client, monkeypatch):
    thread = ThreadFactory()
    place = PlaceFactory()
    ThreadPlaceFactory(thread=thread, place=place, role="subject")
    monkeypatch.setattr("watch.views.enqueue_enrichment", Mock(return_value="job-1"))

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
    enqueue = Mock(return_value="job-1")
    monkeypatch.setattr("watch.views.enqueue_enrichment", enqueue)

    response = admin_client.post(
        action_url(thread), {"action": "edit_query", "query": "parks"}
    )

    assert response.status_code == 400
    assert not ThreadPlace.objects.filter(query_override="parks").exists()
    enqueue.assert_not_called()


@pytest.mark.django_db
def test_edit_query_rejects_unknown_thread_place(admin_client, monkeypatch):
    thread = ThreadFactory()
    other_thread = ThreadFactory()
    place = PlaceFactory()
    ThreadPlaceFactory(thread=other_thread, place=place, role="subject")
    enqueue = Mock(return_value="job-1")
    monkeypatch.setattr("watch.views.enqueue_enrichment", enqueue)

    response = admin_client.post(
        action_url(thread),
        {"action": "edit_query", "place_id": place.id, "query": "parks"},
    )

    assert response.status_code == 400
    enqueue.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize("query", ["", "   "])
def test_edit_query_requires_a_query(admin_client, monkeypatch, query):
    thread = ThreadFactory()
    place = PlaceFactory()
    ThreadPlaceFactory(thread=thread, place=place, role="subject")
    enqueue = Mock(return_value="job-1")
    monkeypatch.setattr("watch.views.enqueue_enrichment", enqueue)

    response = admin_client.post(
        action_url(thread),
        {"action": "edit_query", "place_id": place.id, "query": query},
    )

    assert response.status_code == 400
    enqueue.assert_not_called()


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
def test_correction_with_mentioned_role_leaves_subreddit_alone(admin_client):
    old_place = PlaceFactory(name="Oldville")
    new_place = PlaceFactory(name="Newville")
    subreddit = SubredditFactory(
        kind="state", source="gazetteer", place=old_place, country="US"
    )
    thread = ThreadFactory(subreddit=subreddit)

    admin_client.post(
        correction_url(thread),
        {
            "place_id": new_place.id,
            "role": "mentioned",
            "matched_text": "Newville",
        },
    )

    subreddit.refresh_from_db()
    assert subreddit.place == old_place
    assert subreddit.kind == "state"
    assert subreddit.source == "gazetteer"
    assert subreddit.reviewed_at is None
    thread_place = ThreadPlace.objects.get(thread=thread, place=new_place)
    assert thread_place.role == "mentioned"
    assert thread_place.source == "manual"
    assert PlaceAlias.objects.filter(place=new_place, key="newville").exists()


@pytest.mark.django_db
def test_subject_correction_syncs_subreddit_place_metadata(admin_client):
    old_place = PlaceFactory(name="Toronto", state="ON", country="CA")
    new_place = PlaceFactory(name="Chico", state="CA", country="US")
    subreddit = SubredditFactory(
        kind="state", source="gazetteer", place=old_place, country="CA"
    )
    thread = ThreadFactory(subreddit=subreddit)

    admin_client.post(
        correction_url(thread), {"place_id": new_place.id, "role": "subject"}
    )

    subreddit.refresh_from_db()
    thread.refresh_from_db()
    assert subreddit.kind == "place"
    assert subreddit.state == "CA"
    assert str(subreddit.country) == "US"
    assert subreddit.source == "manual"
    assert subreddit.reviewed_at is not None
    assert thread_country(thread) == "US"


@pytest.mark.django_db
def test_subject_correction_clears_country_when_place_has_none(admin_client):
    old_place = PlaceFactory(name="Oldville", state="CA", country="US")
    new_place = PlaceFactory(name="Nowhere", state="", country="")
    subreddit = SubredditFactory(
        kind="state", source="gazetteer", place=old_place, country="US"
    )
    thread = ThreadFactory(subreddit=subreddit)

    admin_client.post(
        correction_url(thread), {"place_id": new_place.id, "role": "subject"}
    )

    subreddit.refresh_from_db()
    assert subreddit.kind == "place"
    assert subreddit.state == ""
    assert not subreddit.country


@pytest.mark.django_db
def test_correction_rolls_back_when_a_later_write_fails(admin_client, monkeypatch):
    old_place = PlaceFactory(name="Oldville")
    new_place = PlaceFactory(name="Newville")
    subreddit = SubredditFactory(
        kind="state", source="gazetteer", place=old_place, country="US"
    )
    thread = ThreadFactory(subreddit=subreddit)
    monkeypatch.setattr(
        "watch.views.normalize_key", Mock(side_effect=RuntimeError("boom"))
    )

    with pytest.raises(RuntimeError):
        admin_client.post(
            correction_url(thread),
            {
                "place_id": new_place.id,
                "role": "subject",
                "matched_text": "Newville",
            },
        )

    assert not ThreadPlace.objects.filter(thread=thread, place=new_place).exists()
    subreddit.refresh_from_db()
    assert subreddit.place == old_place
    assert subreddit.kind == "state"
    assert subreddit.source == "gazetteer"


@pytest.mark.django_db
def test_triage_hides_mute_country_for_us_threads(admin_client):
    us_thread = ThreadFactory(subreddit=SubredditFactory(country="US"))
    ie_thread = ThreadFactory(subreddit=SubredditFactory(country="IE"))

    us_content = admin_client.get(
        reverse("watch:triage"), {"thread": us_thread.pk}
    ).content.decode()
    ie_content = admin_client.get(
        reverse("watch:triage"), {"thread": ie_thread.pk}
    ).content.decode()

    assert 'data-action="mute_country"' not in us_content
    assert 'data-action="mute_country"' in ie_content


@pytest.mark.django_db
def test_second_correction_updates_the_thread_place_in_place(admin_client):
    thread = ThreadFactory()
    place = PlaceFactory()

    admin_client.post(correction_url(thread), {"place_id": place.id, "role": "subject"})
    admin_client.post(
        correction_url(thread), {"place_id": place.id, "role": "mentioned"}
    )

    thread_places = ThreadPlace.objects.filter(thread=thread, place=place)
    assert thread_places.count() == 1
    assert thread_places.get().role == "mentioned"
    assert thread_places.get().source == "manual"


@pytest.mark.django_db
def test_promote_preserves_an_existing_query_override(admin_client):
    thread = ThreadFactory()
    place = PlaceFactory()
    ThreadPlaceFactory(
        thread=thread,
        place=place,
        role="mentioned",
        query_override="custom budget",
    )

    admin_client.post(action_url(thread), {"action": "promote", "place_id": place.id})

    thread_place = ThreadPlace.objects.get(thread=thread, place=place)
    assert thread_place.role == "subject"
    assert thread_place.query_override == "custom budget"


@pytest.mark.django_db
def test_correction_preserves_an_existing_query_override(admin_client):
    thread = ThreadFactory()
    place = PlaceFactory()
    ThreadPlaceFactory(
        thread=thread,
        place=place,
        role="subject",
        query_override="custom budget",
    )

    admin_client.post(correction_url(thread), {"place_id": place.id, "role": "subject"})

    thread_place = ThreadPlace.objects.get(thread=thread, place=place)
    assert thread_place.query_override == "custom budget"


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
def test_correction_htmx_rerenders_the_current_thread(admin_client):
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
    assert "watch/partials/triage_card.html" in [t.name for t in response.templates]
    # Scope to the detail card; the OOB list refresh may mention other threads.
    card = content.split("</article>", 1)[0]
    assert "First thread" in card
    assert "Second thread" not in card
    assert 'id="triage-detail"' not in content


@pytest.mark.django_db
def test_action_requires_a_csrf_token(superuser):
    client = Client(enforce_csrf_checks=True)
    client.force_login(superuser)
    thread = ThreadFactory()

    rejected = client.post(action_url(thread), {"action": "replied"})
    assert rejected.status_code == 403
    thread.refresh_from_db()
    assert thread.status == "new"

    client.get(reverse("watch:triage"), {"thread": thread.pk})
    token = client.cookies["csrftoken"].value
    accepted = client.post(
        action_url(thread),
        {"action": "replied"},
        HTTP_X_CSRFTOKEN=token,
    )
    assert accepted.status_code == 302
    thread.refresh_from_db()
    assert thread.status == "replied"


@pytest.mark.django_db
def test_authenticated_non_staff_cannot_post(authenticated_client):
    thread = ThreadFactory()
    place = PlaceFactory()

    action_response = authenticated_client.post(
        action_url(thread), {"action": "replied"}
    )
    correction_response = authenticated_client.post(
        correction_url(thread), {"place_id": place.id, "role": "subject"}
    )

    assert action_response.status_code in {302, 403}
    assert correction_response.status_code in {302, 403}
    thread.refresh_from_db()
    assert thread.status == "new"
    assert not ThreadPlace.objects.filter(thread=thread).exists()


@pytest.mark.django_db
def test_unknown_thread_is_404_for_actions_and_corrections(admin_client):
    unknown = uuid.uuid4()
    place = PlaceFactory()

    assert (
        admin_client.post(
            reverse("watch:action", args=[unknown]), {"action": "replied"}
        ).status_code
        == 404
    )
    assert (
        admin_client.post(
            reverse("watch:place-correct", args=[unknown]),
            {"place_id": place.id, "role": "subject"},
        ).status_code
        == 404
    )


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
    assert content.count('hx-target="#triage-detail"') >= len(actions)
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
    assert 'hx-target="#triage-detail"' in content
    # The typeahead searches places rather than pre-setting a place id.
    assert f'hx-get="{reverse("watch:place-search")}"' in content
    assert 'name="matched_text"' in content
    assert 'name="role"' in content
    assert 'data-action="no_place"' in content


def rejection_url(thread):
    return reverse("watch:place-reject", args=[thread.id])


@pytest.mark.django_db
def test_reject_place_hides_suggestion_and_records_label(admin_client):
    thread = ThreadFactory()
    place = PlaceFactory(name="Wrongville")
    ThreadPlaceFactory(thread=thread, place=place, role="subject")

    response = admin_client.post(rejection_url(thread), {"place_id": place.id})

    assert response.status_code == 302
    label = ThreadLabel.objects.get(thread=thread, place=place)
    assert label.is_useful is False
    assert label.is_subject is False
    # The extraction is kept so the decision stays auditable.
    assert ThreadPlace.objects.filter(thread=thread, place=place).exists()


@pytest.mark.django_db
def test_rejected_place_moves_out_of_open_suggestions(admin_client):
    thread = ThreadFactory()
    place = PlaceFactory(name="Wrongville")
    ThreadPlaceFactory(thread=thread, place=place, role="subject")
    ThreadLabel.objects.create(thread=thread, place=place, is_useful=False)

    response = admin_client.get(reverse("watch:triage"), {"thread": thread.pk})
    context = response.context

    assert place not in [tp.place for tp in context["subject_places"]]
    assert [tp.place for tp in context["rejected_places"]] == [place]
    content = response.content.decode()
    assert "Rejected suggestions" in content
    assert "Wrongville" in content


@pytest.mark.django_db
def test_reject_place_rejects_a_place_from_another_thread(admin_client):
    thread = ThreadFactory()
    other = ThreadFactory()
    place = PlaceFactory()
    ThreadPlaceFactory(thread=other, place=place, role="subject")

    response = admin_client.post(rejection_url(thread), {"place_id": place.id})

    assert response.status_code == 400
    assert not ThreadLabel.objects.filter(thread=thread).exists()


@pytest.mark.django_db
def test_reject_place_requires_a_place_id(admin_client):
    thread = ThreadFactory()
    assert admin_client.post(rejection_url(thread), {}).status_code == 400


@pytest.mark.django_db
def test_reject_place_requires_staff(client):
    thread = ThreadFactory()
    place = PlaceFactory()
    ThreadPlaceFactory(thread=thread, place=place, role="subject")
    assert client.post(rejection_url(thread), {"place_id": place.id}).status_code in (
        302,
        403,
    )
    assert not ThreadLabel.objects.exists()


@pytest.mark.django_db
def test_correction_undoes_a_rejection(admin_client):
    thread = ThreadFactory()
    place = PlaceFactory(name="Wrongville")
    ThreadPlaceFactory(thread=thread, place=place, role="subject")
    ThreadLabel.objects.create(thread=thread, place=place, is_useful=False)

    response = admin_client.post(
        correction_url(thread), {"place_id": place.id, "role": "subject"}
    )

    assert response.status_code == 302
    label = ThreadLabel.objects.get(thread=thread, place=place)
    assert label.is_useful is True
    assert label.is_subject is True


@pytest.mark.django_db
def test_correction_is_harmless_when_place_has_no_label(admin_client):
    thread = ThreadFactory()
    place = PlaceFactory()
    admin_client.post(correction_url(thread), {"place_id": place.id})
    assert ThreadLabel.objects.filter(thread=thread, place=place).count() == 1


@pytest.mark.django_db
def test_triage_card_explains_promote(admin_client):
    thread = ThreadFactory()
    place = PlaceFactory()
    ThreadPlaceFactory(thread=thread, place=place, role="mentioned")

    content = admin_client.get(
        reverse("watch:triage"), {"thread": thread.pk}
    ).content.decode()

    # The promote button carries a tooltip explaining what promoting does.
    assert 'data-action="promote"' in content
    assert "subject place" in content
    assert "backlog ranking" in content
