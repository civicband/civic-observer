"""Tests for grouping the same story posted to several subreddits."""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from tests.factories import (
    PlaceFactory,
    SubredditFactory,
    ThreadFactory,
    ThreadPlaceFactory,
)
from watch.ingest import group_crosspost, ingest_page, normalize_title
from watch.models import Mention, MutedCountry, Thread
from watch.scoring import backlog_thread_count, queue_queryset


def _item(alert_id, *, subreddit, post_id, title):
    return {
        "id": alert_id,
        "url": f"https://www.reddit.com/r/{subreddit}/comments/{post_id}/example/",
        "title": f"civic - Reddit Posts - {title}",
        "content_html": "A <strong>civic</strong> meeting",
        "date_published": timezone.now().isoformat(),
        "username": "resident",
        "group": "group",
        "tags": ["civic", "Reddit Posts"],
    }


@pytest.mark.django_db
def test_crosspost_in_another_subreddit_is_grouped():
    canonical = ThreadFactory(
        title="Massive data center proposed",
        subreddit=SubredditFactory(name="sandiego"),
    )
    duplicate = ThreadFactory(
        title="Massive data center proposed!",
        subreddit=SubredditFactory(name="california"),
    )
    assert group_crosspost(duplicate) == canonical
    duplicate.refresh_from_db()
    assert duplicate.duplicate_of == canonical
    assert duplicate not in queue_queryset()


@pytest.mark.django_db
def test_same_title_outside_window_is_separate():
    ThreadFactory(
        title="City council delays the annual budget vote",
        subreddit=SubredditFactory(name="a"),
        last_seen=timezone.now() - timedelta(days=3),
    )
    new = ThreadFactory(
        title="City council delays the annual budget vote",
        subreddit=SubredditFactory(name="b"),
    )
    assert group_crosspost(new) is None


@pytest.mark.django_db
def test_same_subreddit_is_not_grouped():
    subreddit = SubredditFactory(name="a")
    ThreadFactory(
        title="City council delays the annual budget vote",
        subreddit=subreddit,
    )
    new = ThreadFactory(
        title="City council delays the annual budget vote",
        subreddit=subreddit,
    )
    assert group_crosspost(new) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Massive data center proposed!", "massive data center proposed"),
        ("  Massive   Data Center Proposed  ", "massive data center proposed"),
        (
            "Massive data center proposed... https://example.com/story",
            "massive data center proposed",
        ),
        ("CROSSPOST: Massive data center proposed", "massive data center proposed"),
        ("X-Post: Massive data center proposed", "massive data center proposed"),
        ("Repost - Massive data center proposed", "massive data center proposed"),
        ("Weekly Thread", "weekly thread"),
    ],
)
def test_normalize_title(raw, expected):
    assert normalize_title(raw) == expected


@pytest.mark.django_db
def test_short_titles_are_never_grouped():
    ThreadFactory(title="City budget vote", subreddit=SubredditFactory(name="a"))
    new = ThreadFactory(title="City budget vote", subreddit=SubredditFactory(name="b"))
    assert group_crosspost(new) is None


@pytest.mark.django_db
def test_generic_titles_are_never_grouped():
    ThreadFactory(title="Megathread", subreddit=SubredditFactory(name="a"))
    new = ThreadFactory(title="MEGATHREAD", subreddit=SubredditFactory(name="b"))
    assert normalize_title(new.title) == "megathread"
    assert group_crosspost(new) is None


@pytest.mark.django_db
def test_generic_titles_never_group_without_the_length_floor(monkeypatch):
    monkeypatch.setattr("watch.ingest.MIN_CROSSPOST_TITLE_LENGTH", 1)
    canonical = ThreadFactory(
        title="Weekly Thread", subreddit=SubredditFactory(name="a")
    )
    new = ThreadFactory(title="Weekly Thread!", subreddit=SubredditFactory(name="b"))

    assert group_crosspost(new) is None
    assert new.duplicate_of is None
    assert new.duplicate_of != canonical


@pytest.mark.django_db
def test_muted_canonical_subreddit_does_not_swallow_a_visible_crosspost():
    canonical = ThreadFactory(
        title="Massive data center proposed",
        subreddit=SubredditFactory(name="muted", muted=True),
    )
    new = ThreadFactory(
        title="Massive data center proposed!",
        subreddit=SubredditFactory(name="visible"),
    )

    assert group_crosspost(new) is None
    assert new.duplicate_of is None
    assert new.duplicate_of != canonical
    assert new in queue_queryset()


@pytest.mark.django_db
def test_muted_canonical_country_does_not_swallow_a_visible_crosspost():
    MutedCountry.objects.create(code="CA")
    canonical = ThreadFactory(
        title="Massive data center proposed",
        subreddit=SubredditFactory(name="canada", country="CA"),
    )
    new = ThreadFactory(
        title="Massive data center proposed!",
        subreddit=SubredditFactory(name="visible", country="US"),
    )

    assert group_crosspost(new) is None
    assert new.duplicate_of is None
    assert new.duplicate_of != canonical
    assert new in queue_queryset()


@pytest.mark.django_db
def test_only_canonical_threads_can_be_crosspost_roots():
    now = timezone.now()
    canonical = ThreadFactory(
        title="Massive data center proposed",
        subreddit=SubredditFactory(name="canonical"),
        first_seen=now - timedelta(hours=1),
    )
    earlier_duplicate = ThreadFactory(
        title="Massive data center proposed",
        subreddit=SubredditFactory(name="earlier"),
        first_seen=now - timedelta(hours=4),
        duplicate_of=canonical,
    )
    new = ThreadFactory(
        title="Crosspost: Massive data center proposed",
        subreddit=SubredditFactory(name="new"),
    )

    assert group_crosspost(new) == canonical
    new.refresh_from_db()
    assert new.duplicate_of == canonical
    assert new.duplicate_of != earlier_duplicate


@pytest.mark.django_db
def test_multiple_candidates_attach_to_the_earliest_thread():
    now = timezone.now()
    later = ThreadFactory(
        title="Massive data center proposed",
        subreddit=SubredditFactory(name="later"),
        first_seen=now - timedelta(hours=2),
    )
    earliest = ThreadFactory(
        title="Massive data center proposed!",
        subreddit=SubredditFactory(name="earliest"),
        first_seen=now - timedelta(hours=4),
    )
    new = ThreadFactory(
        title="Crosspost: Massive data center proposed",
        subreddit=SubredditFactory(name="new"),
    )

    assert group_crosspost(new) == earliest
    new.refresh_from_db()
    assert new.duplicate_of == earliest
    assert new.duplicate_of != later


@pytest.mark.django_db
def test_ingest_groups_a_newly_created_crosspost():
    page = {
        "items": [
            _item(
                "alert-1",
                subreddit="sandiego",
                post_id="aaa111",
                title="Massive data center proposed",
            ),
            _item(
                "alert-2",
                subreddit="california",
                post_id="bbb222",
                title="Crosspost: Massive data center proposed!",
            ),
        ]
    }

    assert ingest_page(page) == (0, 2, 0)

    canonical = Thread.objects.get(subreddit__name="sandiego")
    duplicate = Thread.objects.get(subreddit__name="california")
    assert duplicate.duplicate_of == canonical


@pytest.mark.django_db
def test_ingest_does_not_regroup_an_existing_thread():
    ThreadFactory(
        title="Massive data center proposed",
        subreddit=SubredditFactory(name="california"),
    )
    existing = ThreadFactory(
        key="reddit:sandiego/aaa111",
        title="Massive data center proposed",
        subreddit=SubredditFactory(name="sandiego"),
    )

    assert ingest_page(
        {
            "items": [
                _item(
                    "alert-2",
                    subreddit="sandiego",
                    post_id="aaa111",
                    title="Massive data center proposed",
                )
            ]
        }
    ) == (0, 1, 0)

    assert Thread.objects.count() == 2
    mention = Mention.objects.get(pk="alert-2")
    assert mention.thread_id == existing.pk
    existing.refresh_from_db()
    assert existing.mention_count == 1
    assert existing.duplicate_of is None


@pytest.mark.django_db
def test_queue_hides_duplicates_but_backlog_counts_them():
    place = PlaceFactory()
    canonical = ThreadFactory(
        title="Massive data center proposed",
        subreddit=SubredditFactory(name="sandiego"),
    )
    duplicate = ThreadFactory(
        title="Massive data center proposed!",
        subreddit=SubredditFactory(name="california"),
        duplicate_of=canonical,
    )
    ThreadPlaceFactory(thread=canonical, place=place, role="subject")
    ThreadPlaceFactory(thread=duplicate, place=place, role="subject")

    assert list(queue_queryset()) == [canonical]
    assert backlog_thread_count() == 2


@pytest.mark.django_db
def test_triage_card_lists_where_else_the_story_was_posted(admin_client):
    canonical = ThreadFactory(
        title="Massive data center proposed",
        subreddit=SubredditFactory(name="sandiego"),
    )
    ThreadFactory(
        title="Massive data center proposed!",
        subreddit=SubredditFactory(name="california"),
        duplicate_of=canonical,
    )

    content = admin_client.get(
        reverse("watch:triage"), {"thread": canonical.pk}
    ).content.decode()

    assert "Also posted in" in content
    assert "r/california" in content


@pytest.mark.django_db
def test_triage_card_omits_crosspost_list_when_there_are_no_duplicates(
    admin_client,
):
    canonical = ThreadFactory(
        title="Massive data center proposed",
        subreddit=SubredditFactory(name="sandiego"),
    )

    content = admin_client.get(
        reverse("watch:triage"), {"thread": canonical.pk}
    ).content.decode()

    assert "Also posted in" not in content
