"""Tests for Thread admin filters, search, and bulk actions."""

import re
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
    uncovered_with_place = ThreadFactory()
    ThreadPlaceFactory(
        thread=uncovered_with_place, place=PlaceFactory(muni=None), role="subject"
    )
    uncovered_without_places = ThreadFactory()

    assert changelist_queryset(admin_client, {"covered": "yes"}) == [covered]
    assert set(changelist_queryset(admin_client, {"covered": "no"})) == {
        uncovered_with_place,
        uncovered_without_places,
    }


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
    # Negation in Django adds ``IS NOT NULL`` guards, so threads whose
    # subreddit or country is unknown fall into the Non-US bucket.
    null_subreddit = ThreadFactory(subreddit=None)
    null_country = ThreadFactory(subreddit=SubredditFactory(country=None))

    assert set(changelist_queryset(admin_client, {"us": "yes"})) == {
        us_country,
        us_place,
    }
    assert set(changelist_queryset(admin_client, {"us": "no"})) == {
        non_us,
        null_subreddit,
        null_country,
    }


@pytest.mark.django_db
def test_thread_admin_status_kind_and_muted_filters(admin_client):
    dismissed = ThreadFactory(
        status="dismissed", subreddit=SubredditFactory(kind="place", muted=True)
    )
    ThreadFactory(status="new", subreddit=SubredditFactory(kind="non_us"))

    assert changelist_queryset(admin_client, {"status__exact": "dismissed"}) == [
        dismissed
    ]
    assert changelist_queryset(admin_client, {"subreddit__kind__exact": "place"}) == [
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
    unselected = ThreadFactory(status="snoozed", snoozed_until=timezone.now())

    response = run_action(admin_client, "mark_replied", [thread])

    thread.refresh_from_db()
    unselected.refresh_from_db()
    assert thread.status == "replied"
    assert thread.snoozed_until is None
    assert unselected.status == "snoozed"
    assert unselected.snoozed_until is not None
    assert message_texts(response) == ["Marked 1 thread(s) as replied."]


@pytest.mark.django_db
def test_mark_dismissed_action(admin_client):
    thread = ThreadFactory(status="snoozed", snoozed_until=timezone.now())
    unselected = ThreadFactory()

    response = run_action(admin_client, "mark_dismissed", [thread])

    thread.refresh_from_db()
    unselected.refresh_from_db()
    assert thread.status == "dismissed"
    assert thread.snoozed_until is None
    assert thread.dismissed_at is not None
    assert unselected.status == "new"
    assert message_texts(response) == ["Marked 1 thread(s) as dismissed."]


@pytest.mark.django_db
def test_snooze_seven_days_action(admin_client):
    thread = ThreadFactory()
    unselected = ThreadFactory()

    response = run_action(admin_client, "snooze_seven_days", [thread])

    thread.refresh_from_db()
    unselected.refresh_from_db()
    assert thread.status == "snoozed"
    assert thread.snoozed_until > timezone.now() + timedelta(days=6)
    assert thread.snoozed_until < timezone.now() + timedelta(days=8)
    assert unselected.status == "new"
    assert unselected.snoozed_until is None
    assert message_texts(response) == ["Snoozed 1 thread(s) for seven days."]


@pytest.mark.django_db
def test_send_to_backlog_action(admin_client):
    thread = ThreadFactory(status="snoozed", snoozed_until=timezone.now())
    unselected = ThreadFactory(status="snoozed", snoozed_until=timezone.now())

    response = run_action(admin_client, "send_to_backlog", [thread])

    thread.refresh_from_db()
    unselected.refresh_from_db()
    assert thread.status == "backlog"
    assert thread.snoozed_until is None
    assert unselected.status == "snoozed"
    assert unselected.snoozed_until is not None
    assert message_texts(response) == ["Sent 1 thread(s) to the backlog."]


@pytest.mark.django_db
def test_mute_subreddits_action_mutes_and_counts_only_unmuted_subreddits(
    admin_client,
):
    shared = SubredditFactory()
    other = SubredditFactory()
    already_muted = SubredditFactory(muted=True)
    unrelated = SubredditFactory()
    muted_at = timezone.now() - timedelta(days=1)
    Subreddit.objects.filter(pk=already_muted.pk).update(modified=muted_at)
    first = ThreadFactory(subreddit=shared)
    second = ThreadFactory(subreddit=shared)
    third = ThreadFactory(subreddit=other)
    fourth = ThreadFactory(subreddit=already_muted)
    ThreadFactory(subreddit=unrelated)

    response = run_action(
        admin_client, "mute_subreddits", [first, second, third, fourth]
    )

    shared.refresh_from_db()
    other.refresh_from_db()
    already_muted.refresh_from_db()
    unrelated.refresh_from_db()
    assert shared.muted is True
    assert other.muted is True
    assert already_muted.muted is True
    assert already_muted.modified == muted_at
    assert unrelated.muted is False
    assert message_texts(response) == ["Muted 2 subreddit(s)."]


@pytest.mark.django_db
def test_mute_subreddits_action_skips_threads_without_a_subreddit(admin_client):
    thread = ThreadFactory(subreddit=None)

    response = run_action(admin_client, "mute_subreddits", [thread])

    assert response.status_code == 200
    assert not Subreddit.objects.filter(muted=True).exists()
    assert message_texts(response) == ["Muted 0 subreddit(s)."]


@pytest.mark.django_db
def test_rerun_enrichment_action_enqueues_each_thread(admin_client, monkeypatch):
    enqueue = Mock(return_value="job-1")
    monkeypatch.setattr("watch.admin.enqueue_enrichment", enqueue)
    first = ThreadFactory()
    second = ThreadFactory()
    unselected = ThreadFactory()

    response = run_action(admin_client, "rerun_enrichment", [first, second])

    assert enqueue.call_count == 2
    assert {call.args[0] for call in enqueue.call_args_list} == {
        str(first.id),
        str(second.id),
    }
    assert str(unselected.id) not in {call.args[0] for call in enqueue.call_args_list}
    assert message_texts(response) == ["Enqueued enrichment for 2 thread(s)."]


@pytest.mark.django_db
def test_rerun_enrichment_action_reports_queue_failures(admin_client, monkeypatch):
    monkeypatch.setattr("watch.admin.enqueue_enrichment", Mock(return_value=None))
    thread = ThreadFactory()

    response = run_action(admin_client, "rerun_enrichment", [thread])

    assert response.status_code == 200
    assert message_texts(response) == [
        "Could not enqueue enrichment for 1 thread(s); is the queue available?"
    ]


@pytest.mark.django_db
def test_rerun_enrichment_action_reports_mixed_outcomes(admin_client, monkeypatch):
    enqueue = Mock(side_effect=["job-1", None])
    monkeypatch.setattr("watch.admin.enqueue_enrichment", enqueue)
    first = ThreadFactory()
    second = ThreadFactory()

    response = run_action(admin_client, "rerun_enrichment", [first, second])

    assert enqueue.call_count == 2
    assert message_texts(response) == [
        "Enqueued enrichment for 1 thread(s).",
        "Could not enqueue enrichment for 1 thread(s); is the queue available?",
    ]


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
        "has_label",
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


@pytest.mark.django_db
def test_thread_admin_exposes_duplicate_grouping_read_only(admin_client):
    canonical = ThreadFactory(
        title="Canonical story", subreddit=SubredditFactory(name="canonical")
    )
    duplicate = ThreadFactory(
        title="Duplicate story",
        subreddit=SubredditFactory(name="duplicate"),
        duplicate_of=canonical,
    )

    change = admin_client.get(reverse("admin:watch_thread_change", args=[duplicate.pk]))
    assert change.status_code == 200
    assert 'name="duplicate_of"' not in change.content.decode()

    changelist = admin_client.get(changelist_url())
    assert changelist.status_code == 200
    cells = re.findall(
        r'<td class="[^"]*\bfield-duplicate_of\b[^"]*">(.*?)</td>',
        changelist.content.decode(),
        re.DOTALL,
    )
    assert any("Canonical story" in cell for cell in cells)

    model_admin = admin.site._registry[Thread]
    assert "duplicate_of" in model_admin.list_display
    assert "duplicate_of" in model_admin.list_select_related
    assert "duplicate_of" in model_admin.readonly_fields  # type: ignore[attr-defined]
