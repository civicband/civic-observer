"""Ranked queries behind the staff triage queue and the backlog page.

The queue score favours threads we can act on: a covered place, recent
activity, and an explicit question.  Mention volume is capped so a noisy
thread cannot bury everything else.  The backlog goes the other way: it
surfaces uncovered places that keep appearing as thread subjects.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any, cast

from django.db.models import (
    Case,
    Count,
    Exists,
    ExpressionWrapper,
    F,
    IntegerField,
    OuterRef,
    Q,
    QuerySet,
    Value,
    When,
)
from django.db.models.expressions import Combinable
from django.db.models.functions import Least
from django.utils import timezone

from watch.models import Mention, MutedCountry, Place, Thread, ThreadPlace

#: Points awarded when a subject place is linked to a municipality.
COVERED_WEIGHT = 100

#: Points awarded when the thread was seen in the last 24 hours.
RECENT_WEIGHT = 30

#: Points awarded when the title or a snippet asks a question.
QUESTION_WEIGHT = 15

#: Maximum contribution from the thread's mention count.
MENTION_CAP = 10

#: How far back a thread still counts towards backlog ranking.
BACKLOG_DAYS = 30

#: How far back a thread counts as recently active.
RECENT_HOURS = 24


def _choice_int(value: Q | Exists | Combinable) -> Case:
    """Return a ``0``/``1`` annotation for a conditional expression."""
    return Case(
        When(value, then=Value(1)),
        default=Value(0),
        output_field=IntegerField(),
    )


def _muted_country_codes():
    """Return the muted country codes as a subquery."""
    return MutedCountry.objects.values("code")


def _country_allowed_q(prefix: str) -> Q:
    """Return a condition matching threads not muted by country.

    The subreddit's explicit ``country`` wins, matching
    :func:`thread_country`.  Only when it is empty or null does the linked
    place's country decide whether the thread is muted.
    """
    country = f"{prefix}__country"
    place_country = f"{prefix}__place__country"
    muted_countries = _muted_country_codes()

    has_country = Q(**{f"{country}__isnull": False}) & ~Q(**{country: ""})
    option = Q(**{f"{place_country}__isnull": True}) | ~Q(
        **{f"{place_country}__in": muted_countries}
    )
    return (has_country & ~Q(**{f"{country}__in": muted_countries})) | (
        ~has_country & option
    )


def _annotate_score(queryset: QuerySet[Thread], now=None) -> QuerySet[Thread]:
    """Annotate ``Thread`` rows with the queue score components."""
    now = now or timezone.now()
    covered = Exists(
        ThreadPlace.objects.filter(
            thread=OuterRef("pk"),
            role=ThreadPlace.Role.SUBJECT,
            place__muni__isnull=False,
        )
    )
    recent_cutoff = now - timedelta(hours=RECENT_HOURS)
    question_snippet = Exists(
        Mention.objects.filter(thread=OuterRef("pk"), snippet_text__contains="?")
    )
    asks_question = Q(title__contains="?") | question_snippet

    return queryset.annotate(
        is_covered=_choice_int(covered),
        is_recent=_choice_int(Q(last_seen__gte=recent_cutoff)),
        has_question=_choice_int(asks_question),
    ).annotate(
        score=ExpressionWrapper(
            F("is_covered") * COVERED_WEIGHT
            + F("is_recent") * RECENT_WEIGHT
            + F("has_question") * QUESTION_WEIGHT
            + Least(F("mention_count"), Value(MENTION_CAP)),
            output_field=IntegerField(),
        )
    )


def queue_queryset() -> QuerySet[Thread]:
    """Return threads ready for triage, ranked by urgency.

    New threads and snoozed threads whose snooze has elapsed are eligible.
    Threads from muted subreddits or muted countries are hidden.  Annotates
    ``is_covered``, ``is_recent``, ``has_question`` and ``score``.
    """
    now = timezone.now()
    eligible = (
        Thread.objects.filter(
            Q(status=Thread.Status.NEW)
            | Q(status=Thread.Status.SNOOZED, snoozed_until__lte=now)
        )
        .filter(Q(subreddit__isnull=True) | Q(subreddit__muted=False))
        .filter(_country_allowed_q("subreddit"))
    )
    return _annotate_score(eligible, now=now).order_by("-score", "-last_seen", "-id")


def next_thread(after_id: uuid.UUID | None = None) -> Thread | None:
    """Return the first queued thread, or the one following ``after_id``.

    The cursor is scored even when it is no longer queued, because actions
    such as replying or dismissing remove it before the next page is
    requested.  If the cursor id does not exist, the head of the queue is
    returned so navigation always lands somewhere useful.
    """
    queryset = queue_queryset()
    if after_id is None:
        return queryset.first()

    cursor = _annotate_score(Thread.objects.filter(pk=after_id)).first()
    if cursor is None:
        return queryset.first()

    cursor_score = cast(Any, cursor).score
    return queryset.filter(
        Q(score__lt=cursor_score)
        | Q(score=cursor_score, last_seen__lt=cursor.last_seen)
        | Q(
            score=cursor_score,
            last_seen=cursor.last_seen,
            id__lt=cursor.pk,
        )
    ).first()


def backlog_queryset() -> QuerySet[Place]:
    """Return uncovered places ranked by recent subject-thread activity."""
    cutoff = timezone.now() - timedelta(days=BACKLOG_DAYS)
    prefix = "thread_places__thread__subreddit"

    active_thread = (
        Q(thread_places__role=ThreadPlace.Role.SUBJECT)
        & Q(thread_places__thread__last_seen__gte=cutoff)
        & (
            Q(thread_places__thread__subreddit__isnull=True)
            | Q(thread_places__thread__subreddit__muted=False)
        )
        & _country_allowed_q(prefix)
    )

    return (
        Place.objects.filter(muni__isnull=True)
        .annotate(
            subject_threads=Count(
                "thread_places__thread", distinct=True, filter=active_thread
            ),
            subject_subreddits=Count(
                "thread_places__thread__subreddit",
                distinct=True,
                filter=active_thread,
            ),
        )
        .filter(subject_threads__gt=0)
        .order_by(
            "-subject_threads",
            "-subject_subreddits",
            F("population").desc(nulls_last=True),
        )
    )


def thread_country(thread: Thread) -> str | None:
    """Return the thread's country code, from its subreddit or place."""
    subreddit = thread.subreddit
    if subreddit is None:
        return None
    if subreddit.country:
        return str(subreddit.country)
    if subreddit.place is not None and subreddit.place.country:
        return str(subreddit.place.country)
    return None
