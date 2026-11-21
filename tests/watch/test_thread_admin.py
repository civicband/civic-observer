"""Tests for Thread admin filters, search, and bulk actions."""

from datetime import timedelta
from unittest.mock import Mock

import pytest
from django.contrib import admin
from django.contrib.messages import get_messages
from django.urls import reverse
from django.utils import timezone

from tests.factories import (
    MentionFactory,
    PlaceFactory,
    PlaceWithMuniFactory,
    SubredditFactory,
    ThreadFactory,
    ThreadPlaceFactory,
)
from watch.models import Subreddit, Thread


def changelist_url():
    return reverse("admin:watch_thread_changelist")


def changelist_queryset(admin_client, params):
    response = admin_client.get(changelist_url(), params)
    assert response.status_code == 200
    return list(response.context["cl"].queryset)


def run_action(admin_client, action, threads):
    return admin_client.post(
        changelist_url(),
        {
            "action": action,
            "_selected_action": [str(thread.pk) for thread in threads],
        },
        follow=True,
    )


def message_texts(response):
    return [str(message) for message in get_messages(response.wsgi_request)]


@pytest.mark.django_db
def test_thread_admin_covered_filter(admin_client):
    covered = ThreadFactory()
    ThreadPlaceFactory(thread=covered, place=PlaceWithMuniFactory(), role="subject")
    uncovered = ThreadFactory()
    ThreadPlaceFactory(thread=uncovered, place=PlaceFactory(muni=None), role="subject")

    assert changelist_queryset(admin_client, {"covered": "yes"}) == [covered]
    assert changelist_queryset(admin_client, {"covered": "no"}) == [uncovered]


@pytest.mark.django_db
def test_thread_admin_covered_filter_ignores_mentioned_and_other_threads(
    admin_client,
):
    mentioned_only = ThreadFactory()
    ThreadPlaceFactory(
        thread=mentioned_only, place=PlaceWithMuniFactory(), role="mentioned"
    )
    covered = ThreadFactory()
    ThreadPlaceFactory(thread=covered, place=PlaceWithMuniFactory(), role="subject")

    assert changelist_queryset(admin_client, {"covered": "yes"}) == [covered]


@pytest.mark.django_db
def test_thread_admin_us_filter_uses_subreddit_country_and_place_country(
    admin_client,
):
    us_country = ThreadFactory(subreddit=SubredditFactory(country="US"))
    us_place = ThreadFactory(
        subreddit=SubredditFactory(place=PlaceFactory(country="US"))
    )
    non_us = ThreadFactory(subreddit=SubredditFactory(country="CA"))

    assert set(changelist_queryset(admin_client, {"us": "yes"})) == {
        us_country,
        us_place,
    }
    assert changelist_queryset(admin_client, {"us": "no"}) == [non_us]


@pytest.mark.django_db
def test_thread_admin_status_kind_and_muted_filters(admin_client):
    dismissed = ThreadFactory(
        status="dismissed", subreddit=SubredditFactory(kind="place", muted=True)
    )
    ThreadFactory(status="new", subreddit=SubredditFactory(kind="non_us"))

    assert changelist_queryset(admin_client, {"status": "dismissed"}) == [dismissed]
    assert changelist_queryset(admin_client, {"subreddit__kind": "place"}) == [
        dismissed
    ]
    assert changelist_queryset(admin_client, {"subreddit__muted__exact": "1"}) == [
        dismissed
    ]


@pytest.mark.django_db
def test_thread_admin_last_seen_filter(admin_client):
    old = ThreadFactory(last_seen=timezone.now() - timedelta(days=400))
    ThreadFactory(last_seen=timezone.now())
    year = str(old.last_seen.year)

    assert changelist_queryset(admin_client, {"last_seen__year": year}) == [old]


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("params", "mention_kwargs"),
    [
        ({"q": "zoning"}, {"snippet_text": "A zoning question"}),
        ({"q": "budget"}, {"keyword": "budget"}),
    ],
)
def test_thread_admin_search_covers_mention_fields(
    admin_client, params, mention_kwargs
):
    matching = ThreadFactory(title="Unrelated title")
    MentionFactory(thread=matching, **mention_kwargs)
    MentionFactory(thread=ThreadFactory(), snippet_text="nothing here")

    assert changelist_queryset(admin_client, params) == [matching]


@pytest.mark.django_db
def test_thread_admin_search_covers_title_and_subreddit(admin_client):
    by_title = ThreadFactory(title="Council approves budget")
    by_subreddit = ThreadFactory(subreddit=SubredditFactory(name="chicoland"))

    assert changelist_queryset(admin_client, {"q": "approves"}) == [by_title]
    assert changelist_queryset(admin_client, {"q": "chicoland"}) == [by_subreddit]


@pytest.mark.django_db
def test_thread_admin_search_deduplicates_joined_mentions(admin_client):
    thread = ThreadFactory()
    MentionFactory(thread=thread, snippet_text="zoning")
    MentionFactory(thread=thread, snippet_text="zoning again")

    assert changelist_queryset(admin_client, {"q": "zoning"}) == [thread]


@pytest.mark.django_db
def test_mark_replied_action(admin_client):
    thread = ThreadFactory(status="snoozed", snoozed_until=timezone.now())

    response = run_action(admin_client, "mark_replied", [thread])

    thread.refresh_from_db()
    assert thread.status == "replied"
    assert thread.snoozed_until is None
    assert any("replied" in text.lower() for text in message_texts(response))


@pytest.mark.django_db
def test_mark_dismissed_action(admin_client):
    thread = ThreadFactory()

    run_action(admin_client, "mark_dismissed", [thread])

    thread.refresh_from_db()
    assert thread.status == "dismissed"


@pytest.mark.django_db
def test_snooze_seven_days_action(admin_client):
    thread = ThreadFactory()

    run_action(admin_client, "snooze_seven_days", [thread])

    thread.refresh_from_db()
    assert thread.status == "snoozed"
    assert thread.snoozed_until > timezone.now() + timedelta(days=6)
    assert thread.snoozed_until < timezone.now() + timedelta(days=8)


@pytest.mark.django_db
def test_send_to_backlog_action(admin_client):
    thread = ThreadFactory(status="snoozed", snoozed_until=timezone.now())

    run_action(admin_client, "send_to_backlog", [thread])

    thread.refresh_from_db()
    assert thread.status == "backlog"
    assert thread.snoozed_until is None


@pytest.mark.django_db
def test_mute_subreddits_action_mutes_distinct_subreddits(admin_client):
    shared = SubredditFactory()
    other = SubredditFactory()
    first = ThreadFactory(subreddit=shared)
    second = ThreadFactory(subreddit=shared)
    third = ThreadFactory(subreddit=other)

    response = run_action(admin_client, "mute_subreddits", [first, second, third])

    assert set(Subreddit.objects.filter(muted=True)) == {shared, other}
    assert any("2" in text for text in message_texts(response))


@pytest.mark.django_db
def test_mute_subreddits_action_skips_threads_without_a_subreddit(admin_client):
    thread = ThreadFactory(subreddit=None)

    response = run_action(admin_client, "mute_subreddits", [thread])

    assert response.status_code == 200
    assert not Subreddit.objects.filter(muted=True).exists()


@pytest.mark.django_db
def test_rerun_enrichment_action_enqueues_each_thread(admin_client, monkeypatch):
    enqueue = Mock(return_value="job-1")
    monkeypatch.setattr("watch.admin.enqueue_enrichment", enqueue)
    first = ThreadFactory()
    second = ThreadFactory()

    response = run_action(admin_client, "rerun_enrichment", [first, second])

    assert enqueue.call_count == 2
    assert {call.args[0] for call in enqueue.call_args_list} == {
        str(first.id),
        str(second.id),
    }
    assert any("2" in text for text in message_texts(response))


@pytest.mark.django_db
def test_rerun_enrichment_action_reports_queue_failures(admin_client, monkeypatch):
    monkeypatch.setattr("watch.admin.enqueue_enrichment", Mock(return_value=None))
    thread = ThreadFactory()

    response = run_action(admin_client, "rerun_enrichment", [thread])

    assert response.status_code == 200
    texts = message_texts(response)
    assert any("1" in text and "could not" in text.lower() for text in texts)


def test_thread_admin_registers_expected_filters_and_search():
    model_admin = admin.site._registry[Thread]
    parameters = [
        getattr(item, "parameter_name", item) for item in model_admin.list_filter
    ]

    assert parameters == [
        "status",
        "subreddit__kind",
        "covered",
        "us",
        "subreddit__muted",
        "last_seen",
    ]
    assert model_admin.search_fields == [
        "title",
        "mentions__snippet_text",
        "mentions__keyword",
        "subreddit__name",
    ]
    assert set(model_admin.actions) == {
        "mark_replied",
        "mark_dismissed",
        "snooze_seven_days",
        "send_to_backlog",
        "mute_subreddits",
        "rerun_enrichment",
    }
