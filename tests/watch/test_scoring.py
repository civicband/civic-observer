from datetime import timedelta

import pytest
from django.utils import timezone

from tests.factories import (
    MentionFactory,
    PlaceFactory,
    PlaceWithMuniFactory,
    SubredditFactory,
    ThreadFactory,
)
from watch.models import MutedCountry, Thread, ThreadPlace
from watch.scoring import (
    MENTION_CAP,
    RECENT_WEIGHT,
    backlog_queryset,
    next_thread,
    queue_queryset,
    thread_country,
)


def _subject(thread, place):
    return ThreadPlace.objects.create(
        thread=thread,
        place=place,
        role="subject",
        confidence="high",
        source="subreddit",
    )


@pytest.mark.django_db
def test_covered_recent_question_thread_ranks_first():
    covered = ThreadFactory(last_seen=timezone.now())
    _subject(covered, PlaceWithMuniFactory())
    MentionFactory(thread=covered, snippet_text="Will the council vote?")
    ThreadFactory(last_seen=timezone.now() - timedelta(days=3))

    assert list(queue_queryset())[0] == covered


@pytest.mark.django_db
def test_queue_annotations_score_components():
    covered = ThreadFactory(last_seen=timezone.now(), mention_count=3)
    _subject(covered, PlaceWithMuniFactory())
    MentionFactory(thread=covered, snippet_text="Is this on the agenda?")
    recent = ThreadFactory(last_seen=timezone.now() - timedelta(hours=1))
    old = ThreadFactory(last_seen=timezone.now() - timedelta(days=2))

    threads = {thread.pk: thread for thread in queue_queryset()}

    assert threads[covered.pk].is_covered == 1
    assert threads[covered.pk].is_recent == 1
    assert threads[covered.pk].has_question == 1
    assert threads[covered.pk].score == 100 + 30 + 15 + 3
    assert threads[recent.pk].is_covered == 0
    assert threads[recent.pk].is_recent == 1
    assert threads[recent.pk].has_question == 0
    assert threads[recent.pk].score == 30
    assert threads[old.pk].is_recent == 0
    assert threads[old.pk].score == 0


@pytest.mark.django_db
def test_queue_question_from_title():
    thread = ThreadFactory(title="What is happening?")

    assert thread in queue_queryset()
    assert queue_queryset().get(pk=thread.pk).has_question == 1


@pytest.mark.django_db
def test_queue_mention_count_is_capped():
    thread = ThreadFactory(mention_count=50)

    assert queue_queryset().get(pk=thread.pk).score == RECENT_WEIGHT + MENTION_CAP


@pytest.mark.django_db
def test_muted_subreddit_and_country_are_hidden():
    sub = SubredditFactory(muted=True)
    hidden = ThreadFactory(subreddit=sub)
    country = SubredditFactory(country="IE")
    MutedCountry.objects.create(code="IE")
    hidden2 = ThreadFactory(subreddit=country)

    assert hidden not in queue_queryset()
    assert hidden2 not in queue_queryset()


@pytest.mark.django_db
def test_only_new_and_due_snoozed_threads_are_queued():
    new = ThreadFactory(status=Thread.Status.NEW)
    due = ThreadFactory(
        status=Thread.Status.SNOOZED,
        snoozed_until=timezone.now() - timedelta(minutes=1),
    )
    future = ThreadFactory(
        status=Thread.Status.SNOOZED,
        snoozed_until=timezone.now() + timedelta(days=1),
    )
    replied = ThreadFactory(status=Thread.Status.REPLIED)
    dismissed = ThreadFactory(status=Thread.Status.DISMISSED)
    backlog = ThreadFactory(status=Thread.Status.BACKLOG)

    queued = set(queue_queryset())

    assert new in queued
    assert due in queued
    assert future not in queued
    assert replied not in queued
    assert dismissed not in queued
    assert backlog not in queued


@pytest.mark.django_db
def test_snoozed_thread_reappears_after_time():
    thread = ThreadFactory(
        status=Thread.Status.SNOOZED,
        snoozed_until=timezone.now() - timedelta(minutes=1),
    )
    assert thread in queue_queryset()
    thread.snoozed_until = timezone.now() + timedelta(days=1)
    thread.save()
    assert thread not in queue_queryset()


@pytest.mark.django_db
def test_next_thread_walks_the_queue():
    top = ThreadFactory(last_seen=timezone.now(), mention_count=5)
    second = ThreadFactory(last_seen=timezone.now(), mention_count=2)
    third = ThreadFactory(last_seen=timezone.now(), mention_count=1)

    assert next_thread() == top
    assert next_thread(after_id=top.pk) == second
    assert next_thread(after_id=second.pk) == third
    assert next_thread(after_id=third.pk) is None


@pytest.mark.django_db
def test_next_thread_ignores_threads_outside_the_queue():
    only = ThreadFactory(last_seen=timezone.now())
    dismissed = ThreadFactory(status=Thread.Status.DISMISSED)

    assert next_thread(after_id=dismissed.pk) == only
    assert next_thread(after_id=dismissed.pk) == next_thread()


@pytest.mark.django_db
def test_next_thread_is_none_for_an_empty_queue():
    assert next_thread() is None


@pytest.mark.django_db
def test_backlog_only_uncovered_subject_places():
    covered = PlaceWithMuniFactory()
    uncovered = PlaceFactory(name="Nowhere", muni=None)
    _subject(ThreadFactory(), covered)
    _subject(ThreadFactory(), uncovered)

    assert list(backlog_queryset()) == [uncovered]


@pytest.mark.django_db
def test_backlog_ignores_non_subject_and_stale_threads():
    place = PlaceFactory(name="Nowhere", muni=None)
    ThreadPlace.objects.create(
        thread=ThreadFactory(),
        place=place,
        role="mentioned",
        confidence="high",
        source="subreddit",
    )
    _subject(ThreadFactory(last_seen=timezone.now() - timedelta(days=31)), place)

    assert place not in backlog_queryset()


@pytest.mark.django_db
def test_backlog_ignores_muted_subreddits():
    place = PlaceFactory(name="Nowhere", muni=None)
    _subject(ThreadFactory(subreddit=SubredditFactory(muted=True)), place)

    assert place not in backlog_queryset()


@pytest.mark.django_db
def test_backlog_ranks_by_distinct_threads_then_subreddits_then_population():
    quiet = PlaceFactory(name="Quiet", muni=None, population=9000)
    busy = PlaceFactory(name="Busy", muni=None, population=1000)
    sub = SubredditFactory()
    _subject(ThreadFactory(subreddit=sub), busy)
    _subject(ThreadFactory(subreddit=sub), busy)
    _subject(ThreadFactory(subreddit=sub), quiet)
    places = list(backlog_queryset())

    assert places == [busy, quiet]
    assert places[0].subject_threads == 2
    assert places[0].subject_subreddits == 1
    assert places[1].subject_threads == 1


@pytest.mark.django_db
def test_backlog_population_breaks_ties():
    small = PlaceFactory(name="Small", muni=None, population=100)
    large = PlaceFactory(name="Large", muni=None, population=100000)
    _subject(ThreadFactory(), small)
    _subject(ThreadFactory(), large)

    assert list(backlog_queryset()) == [large, small]


@pytest.mark.django_db
def test_thread_country_prefers_subreddit_country():
    thread = ThreadFactory(subreddit=SubredditFactory(country="IE"))

    assert thread_country(thread) == "IE"


@pytest.mark.django_db
def test_thread_country_falls_back_to_place_country():
    place = PlaceFactory(country="CA")
    thread = ThreadFactory(subreddit=SubredditFactory(place=place))

    assert thread_country(thread) == "CA"


@pytest.mark.django_db
def test_thread_country_is_none_without_subreddit_or_country():
    assert thread_country(ThreadFactory()) is None
    assert thread_country(ThreadFactory(subreddit=SubredditFactory())) is None
