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


def queue_queryset() -> QuerySet[Thread]:
    """Return threads ready for triage, ranked by urgency.

    New threads and snoozed threads whose snooze has elapsed are eligible.
    Threads from muted subreddits or muted countries are hidden.  Annotates
    ``is_covered``, ``is_recent``, ``has_question`` and ``score``.
    """
    now = timezone.now()
    muted_countries = _muted_country_codes()

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

    queryset = (
        Thread.objects.filter(
            Q(status=Thread.Status.NEW)
            | Q(status=Thread.Status.SNOOZED, snoozed_until__lte=now)
        )
        .filter(Q(subreddit__isnull=True) | Q(subreddit__muted=False))
        .filter(
            Q(subreddit__country__isnull=True)
            | ~Q(subreddit__country__in=muted_countries)
        )
        .annotate(
            is_covered=_choice_int(covered),
            is_recent=_choice_int(Q(last_seen__gte=recent_cutoff)),
            has_question=_choice_int(asks_question),
        )
        .annotate(
            score=ExpressionWrapper(
                F("is_covered") * COVERED_WEIGHT
                + F("is_recent") * RECENT_WEIGHT
                + F("has_question") * QUESTION_WEIGHT
                + Least(F("mention_count"), Value(MENTION_CAP)),
                output_field=IntegerField(),
            )
        )
        .order_by("-score", "-last_seen")
    )
    return queryset


def next_thread(after_id: uuid.UUID | None = None) -> Thread | None:
    """Return the first queued thread, or the one following ``after_id``.

    If ``after_id`` is not in the queue, the first queued thread is returned
    so navigation always lands somewhere useful.
    """
    queryset = queue_queryset()
    if after_id is None:
        return queryset.first()

    current = queryset.filter(pk=after_id).first()
    if current is None:
        return queryset.first()

    current_score = cast(Any, current).score
    return queryset.filter(
        Q(score__lt=current_score)
        | Q(score=current_score, last_seen__lt=current.last_seen)
    ).first()


def backlog_queryset() -> QuerySet[Place]:
    """Return uncovered places ranked by recent subject-thread activity."""
    cutoff = timezone.now() - timedelta(days=BACKLOG_DAYS)
    muted_countries = _muted_country_codes()

    active_thread = (
        Q(thread_places__role=ThreadPlace.Role.SUBJECT)
        & Q(thread_places__thread__last_seen__gte=cutoff)
        & (
            Q(thread_places__thread__subreddit__isnull=True)
            | Q(thread_places__thread__subreddit__muted=False)
        )
        & (
            Q(thread_places__thread__subreddit__country__isnull=True)
            | ~Q(thread_places__thread__subreddit__country__in=muted_countries)
        )
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
        .order_by("-subject_threads", "-subject_subreddits", "-population")
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
