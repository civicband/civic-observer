import uuid
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
from watch.models import MutedCountry, PlaceNote, Thread, ThreadPlace
from watch.scoring import (
    MENTION_CAP,
    RECENT_WEIGHT,
    backlog_queryset,
    filtered_queue_queryset,
    next_thread,
    previous_thread,
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
def test_queue_is_covered_requires_a_linked_subject_place():
    uncovered = ThreadFactory()
    _subject(uncovered, PlaceFactory(muni=None))
    mentioned = ThreadFactory()
    ThreadPlace.objects.create(
        thread=mentioned,
        place=PlaceWithMuniFactory(),
        role="mentioned",
        confidence="high",
        source="subreddit",
    )

    threads = {thread.pk: thread for thread in queue_queryset()}

    assert threads[uncovered.pk].is_covered == 0
    assert threads[mentioned.pk].is_covered == 0


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
    control = ThreadFactory(subreddit=SubredditFactory(country="US"))

    queued = set(queue_queryset())

    assert hidden not in queued
    assert hidden2 not in queued
    assert control in queued


@pytest.mark.django_db
def test_queue_country_mute_uses_subreddit_country_before_place_country():
    MutedCountry.objects.create(code="IE")
    muted_explicit = ThreadFactory(
        subreddit=SubredditFactory(country="IE", place=PlaceFactory(country="US"))
    )
    allowed_explicit = ThreadFactory(
        subreddit=SubredditFactory(country="US", place=PlaceFactory(country="IE"))
    )
    muted_fallback = ThreadFactory(
        subreddit=SubredditFactory(country=None, place=PlaceFactory(country="IE"))
    )
    muted_empty_fallback = ThreadFactory(
        subreddit=SubredditFactory(country="", place=PlaceFactory(country="IE"))
    )

    queued = set(queue_queryset())

    assert muted_explicit not in queued
    assert allowed_explicit in queued
    assert muted_fallback not in queued
    assert muted_empty_fallback not in queued


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
def test_next_thread_after_a_replied_thread_moves_on():
    top = ThreadFactory(last_seen=timezone.now(), mention_count=5)
    second = ThreadFactory(last_seen=timezone.now(), mention_count=2)
    top.status = Thread.Status.REPLIED
    top.save()

    assert next_thread(after_id=top.pk) == second


@pytest.mark.django_db
def test_next_thread_after_a_dismissed_thread_moves_on():
    top = ThreadFactory(last_seen=timezone.now(), mention_count=5)
    second = ThreadFactory(last_seen=timezone.now(), mention_count=2)
    top.status = Thread.Status.DISMISSED
    top.save()

    assert next_thread(after_id=top.pk) == second


@pytest.mark.django_db
def test_next_thread_returns_head_for_an_unknown_cursor():
    only = ThreadFactory(last_seen=timezone.now())

    assert next_thread(after_id=uuid.uuid4()) == only


@pytest.mark.django_db
def test_next_thread_is_none_for_an_empty_queue():
    assert next_thread() is None


@pytest.mark.django_db
def test_queue_orders_identical_scores_by_id_descending():
    same_seen = timezone.now()
    threads = [ThreadFactory(last_seen=same_seen) for _ in range(3)]

    ordered = [thread.pk for thread in queue_queryset()]

    assert ordered == sorted((thread.pk for thread in threads), reverse=True)


@pytest.mark.django_db
def test_next_thread_follows_identical_score_order():
    same_seen = timezone.now()
    for _ in range(3):
        ThreadFactory(last_seen=same_seen)
    ordered = list(queue_queryset())

    assert next_thread(after_id=ordered[0].pk) == ordered[1]
    assert next_thread(after_id=ordered[1].pk) == ordered[2]
    assert next_thread(after_id=ordered[2].pk) is None


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
def test_backlog_includes_29_day_threads_and_excludes_31_day_threads():
    recent = PlaceFactory(name="Recent", muni=None)
    stale = PlaceFactory(name="Stale", muni=None)
    _subject(ThreadFactory(last_seen=timezone.now() - timedelta(days=29)), recent)
    _subject(ThreadFactory(last_seen=timezone.now() - timedelta(days=31)), stale)

    assert list(backlog_queryset()) == [recent]


@pytest.mark.django_db
def test_backlog_ignores_muted_subreddits():
    place = PlaceFactory(name="Nowhere", muni=None)
    _subject(ThreadFactory(subreddit=SubredditFactory(muted=True)), place)

    assert place not in backlog_queryset()


@pytest.mark.django_db
def test_backlog_country_mute_uses_subreddit_country_before_place_country():
    MutedCountry.objects.create(code="IE")
    muted_explicit = PlaceFactory(name="Muted explicit", muni=None)
    _subject(ThreadFactory(subreddit=SubredditFactory(country="IE")), muted_explicit)
    allowed_explicit = PlaceFactory(name="Allowed explicit", muni=None)
    _subject(
        ThreadFactory(
            subreddit=SubredditFactory(country="US", place=PlaceFactory(country="IE"))
        ),
        allowed_explicit,
    )
    muted_fallback = PlaceFactory(name="Muted fallback", muni=None)
    _subject(
        ThreadFactory(
            subreddit=SubredditFactory(country=None, place=PlaceFactory(country="IE"))
        ),
        muted_fallback,
    )

    assert list(backlog_queryset()) == [allowed_explicit]


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
def test_backlog_distinct_subreddits_breaks_thread_count_ties():
    wider = PlaceFactory(name="Wider", muni=None, population=100)
    narrower = PlaceFactory(name="Narrower", muni=None, population=100000)
    _subject(ThreadFactory(subreddit=SubredditFactory()), wider)
    _subject(ThreadFactory(subreddit=SubredditFactory()), wider)
    shared = SubredditFactory()
    _subject(ThreadFactory(subreddit=shared), narrower)
    _subject(ThreadFactory(subreddit=shared), narrower)

    places = list(backlog_queryset())

    assert places == [wider, narrower]
    assert [place.subject_threads for place in places] == [2, 2]
    assert [place.subject_subreddits for place in places] == [2, 1]


@pytest.mark.django_db
def test_backlog_population_breaks_ties():
    small = PlaceFactory(name="Small", muni=None, population=100)
    large = PlaceFactory(name="Large", muni=None, population=100000)
    _subject(ThreadFactory(), small)
    _subject(ThreadFactory(), large)

    assert list(backlog_queryset()) == [large, small]


@pytest.mark.django_db
def test_backlog_unknown_population_sorts_last():
    known = PlaceFactory(name="Known", muni=None, population=1000)
    unknown = PlaceFactory(name="Unknown", muni=None, population=None)
    _subject(ThreadFactory(), known)
    _subject(ThreadFactory(), unknown)

    assert list(backlog_queryset()) == [known, unknown]


@pytest.mark.django_db
def test_thread_country_prefers_subreddit_country_over_place_country():
    place = PlaceFactory(country="US")
    thread = ThreadFactory(subreddit=SubredditFactory(country="IE", place=place))

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


@pytest.mark.django_db
def test_filtered_queue_covered_filter():
    covered = ThreadFactory(last_seen=timezone.now())
    _subject(covered, PlaceWithMuniFactory())
    uncovered = ThreadFactory(last_seen=timezone.now() - timedelta(minutes=1))

    assert list(filtered_queue_queryset(covered="yes")) == [covered]
    assert list(filtered_queue_queryset(covered="no")) == [uncovered]
    # Unknown value falls back to the whole queue.
    assert set(filtered_queue_queryset(covered="bogus")) == {covered, uncovered}


@pytest.mark.django_db
def test_filtered_queue_kind_filter():
    place_sub = SubredditFactory(kind="place")
    place_thread = ThreadFactory(subreddit=place_sub)
    general_thread = ThreadFactory(subreddit=SubredditFactory(kind="general"))

    assert list(filtered_queue_queryset(kind="place")) == [place_thread]
    assert general_thread not in filtered_queue_queryset(kind="place")


@pytest.mark.django_db
def test_filtered_queue_us_filter():
    us = ThreadFactory(subreddit=SubredditFactory(country="US"))
    non_us = ThreadFactory(subreddit=SubredditFactory(country="IE"))

    assert list(filtered_queue_queryset(us="yes")) == [us]
    assert list(filtered_queue_queryset(us="no")) == [non_us]


@pytest.mark.django_db
def test_filtered_queue_text_search_matches_title_and_subreddit():
    by_title = ThreadFactory(title="Chico council vote")
    by_sub = ThreadFactory(subreddit=SubredditFactory(name="chicoca"))
    other = ThreadFactory(title="Nothing here")

    matched = set(filtered_queue_queryset(q="chico"))
    assert matched == {by_title, by_sub}
    assert other not in matched


@pytest.mark.django_db
def test_filtered_queue_sort_newest_and_oldest():
    newest = ThreadFactory(last_seen=timezone.now())
    oldest = ThreadFactory(last_seen=timezone.now() - timedelta(days=2))

    assert list(filtered_queue_queryset(sort="newest")) == [newest, oldest]
    assert list(filtered_queue_queryset(sort="oldest")) == [oldest, newest]


@pytest.mark.django_db
def test_filtered_next_and_previous_walk_only_the_filtered_queue():
    covered_a = ThreadFactory(last_seen=timezone.now())
    _subject(covered_a, PlaceWithMuniFactory())
    uncovered = ThreadFactory(last_seen=timezone.now() - timedelta(minutes=1))
    covered_b = ThreadFactory(last_seen=timezone.now() - timedelta(minutes=2))
    _subject(covered_b, PlaceWithMuniFactory())

    filtered = filtered_queue_queryset(covered="yes")

    assert next_thread(covered_a.pk, queryset=filtered) == covered_b
    assert previous_thread(covered_b.pk, queryset=filtered) == covered_a
    # The uncovered thread is skipped because it is outside the filter.
    assert next_thread(covered_a.pk, queryset=filtered) != uncovered


def _note(place, status):
    return PlaceNote.objects.create(place=place, text="note", status=status)


@pytest.mark.django_db
def test_backlog_default_hides_judged_places_but_keeps_unnooted():
    open_place = PlaceFactory(name="Open", muni=None)
    added = PlaceFactory(name="Added", muni=None)
    rejected = PlaceFactory(name="Rejected", muni=None)
    _subject(ThreadFactory(), open_place)
    _subject(ThreadFactory(), added)
    _subject(ThreadFactory(), rejected)
    _note(added, PlaceNote.Status.ADDED)
    _note(rejected, PlaceNote.Status.REJECTED)

    names = [place.name for place in backlog_queryset()]
    assert names == ["Open"]


@pytest.mark.django_db
def test_backlog_status_all_shows_everything():
    added = PlaceFactory(name="Added", muni=None)
    _subject(ThreadFactory(), added)
    _note(added, PlaceNote.Status.ADDED)

    assert list(backlog_queryset(status="all")) == [added]


@pytest.mark.django_db
def test_backlog_status_filter_by_latest_note():
    maybe = PlaceFactory(name="Maybe", muni=None)
    added = PlaceFactory(name="Added", muni=None)
    _subject(ThreadFactory(), maybe)
    _subject(ThreadFactory(), added)
    _note(added, PlaceNote.Status.ADDED)
    _note(maybe, PlaceNote.Status.MAYBE)

    assert list(backlog_queryset(status="maybe")) == [maybe]
    assert list(backlog_queryset(status="added")) == [added]


@pytest.mark.django_db
def test_backlog_sort_by_population():
    small = PlaceFactory(name="Small", muni=None, population=100)
    large = PlaceFactory(name="Large", muni=None, population=100000)
    _subject(ThreadFactory(), small)
    _subject(ThreadFactory(), large)

    assert list(backlog_queryset(sort="population")) == [large, small]


@pytest.mark.django_db
def test_backlog_sort_by_recency():
    recent = PlaceFactory(name="Recent", muni=None)
    stale = PlaceFactory(name="Stale", muni=None)
    _subject(ThreadFactory(last_seen=timezone.now()), recent)
    _subject(ThreadFactory(last_seen=timezone.now() - timedelta(days=5)), stale)

    assert list(backlog_queryset(sort="recency")) == [recent, stale]
