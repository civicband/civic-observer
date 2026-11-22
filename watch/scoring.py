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
    Max,
    OuterRef,
    Q,
    QuerySet,
    Subquery,
    Value,
    When,
)
from django.db.models.expressions import Combinable
from django.db.models.functions import Least
from django.utils import timezone

from watch.models import (
    Mention,
    MutedCountry,
    Place,
    PlaceNote,
    Thread,
    ThreadPlace,
)

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


def country_allowed_q(prefix: str) -> Q:
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
    Cross-post duplicates, threads from muted subreddits, and muted countries
    are hidden.  Annotates ``is_covered``, ``is_recent``, ``has_question`` and
    ``score``.
    """
    now = timezone.now()
    eligible = (
        Thread.objects.filter(
            Q(status=Thread.Status.NEW)
            | Q(status=Thread.Status.SNOOZED, snoozed_until__lte=now)
        )
        .filter(duplicate_of__isnull=True)
        .filter(Q(subreddit__isnull=True) | Q(subreddit__muted=False))
        .filter(country_allowed_q("subreddit"))
    )
    return _annotate_score(eligible, now=now).order_by("-score", "-last_seen", "-id")


#: Accepted ``sort`` values for the filtered queue, mapped to their order_by.
QUEUE_SORTS: dict[str, tuple[str, ...]] = {
    "score": ("-score", "-last_seen", "-id"),
    "newest": ("-last_seen", "-id"),
    "oldest": ("last_seen", "id"),
    "mentions": ("-mention_count", "-last_seen", "-id"),
}

#: Accepted ``covered`` filter values for the filtered queue.
QUEUE_COVERED_CHOICES = ("yes", "no")

#: Accepted ``us`` filter values for the filtered queue.
QUEUE_US_CHOICES = ("yes", "no")


def _us_q(prefix: str) -> Q:
    """Return a condition matching threads whose subreddit or place is US.

    Mirrors the admin ``UsFilter``: a US place under a country-less subreddit
    counts as US.
    """
    return Q(**{f"{prefix}__country": "US"}) | Q(**{f"{prefix}__place__country": "US"})


def filtered_queue_queryset(
    *,
    covered: str | None = None,
    kind: str | None = None,
    us: str | None = None,
    sort: str | None = None,
    q: str | None = None,
) -> QuerySet[Thread]:
    """Return :func:`queue_queryset` narrowed by the operator's list filters.

    ``covered`` (``yes``/``no``) keeps only threads with or without a covered
    subject place.  ``kind`` keeps threads whose subreddit kind matches (for
    example ``place`` or ``general``).  ``us`` (``yes``/``no``) mirrors the
    admin's US filter.  ``q`` matches the thread title or subreddit name
    case-insensitively.  ``sort`` chooses one of :data:`QUEUE_SORTS`; an
    unknown value falls back to the default score ranking.  Unknown filter
    values are ignored so a stale bookmark still renders a sensible queue.
    """
    queryset = queue_queryset()

    if covered in QUEUE_COVERED_CHOICES:
        covered_q = Q(is_covered=1 if covered == "yes" else 0)
        queryset = queryset.filter(covered_q)

    if kind:
        queryset = queryset.filter(subreddit__kind=kind)

    if us in QUEUE_US_CHOICES:
        us_q = _us_q("subreddit")
        queryset = queryset.filter(us_q) if us == "yes" else queryset.exclude(us_q)

    if q:
        queryset = queryset.filter(
            Q(title__icontains=q) | Q(subreddit__name__icontains=q)
        )

    order = QUEUE_SORTS.get(sort or "", QUEUE_SORTS["score"])
    return queryset.order_by(*order)


def next_thread(
    after_id: uuid.UUID | None = None,
    *,
    queryset: QuerySet[Thread] | None = None,
) -> Thread | None:
    """Return the first queued thread, or the one following ``after_id``.

    The cursor is scored even when it is no longer queued, because actions
    such as replying or dismissing remove it before the next page is
    requested.  If the cursor id does not exist, the head of the queue is
    returned so navigation always lands somewhere useful.

    ``queryset`` overrides the queue to walk (for example a filtered queue);
    it must be annotated and ordered like :func:`queue_queryset`.  It defaults
    to :func:`queue_queryset`.
    """
    if queryset is None:
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


def previous_thread(
    before_id: uuid.UUID | None = None,
    *,
    queryset: QuerySet[Thread] | None = None,
) -> Thread | None:
    """Return the queued thread immediately before ``before_id``, if any.

    Mirrors :func:`next_thread`: the cursor is scored even when it is no longer
    queued, so navigation also works after an action removed the thread.  An
    unknown cursor, or no ``before_id``, returns ``None``.  ``queryset``
    overrides the queue to walk, as for :func:`next_thread`.
    """
    if before_id is None:
        return None
    if queryset is None:
        queryset = queue_queryset()

    cursor = _annotate_score(Thread.objects.filter(pk=before_id)).first()
    if cursor is None:
        return None

    cursor_score = cast(Any, cursor).score
    return (
        queryset.filter(
            Q(score__gt=cursor_score)
            | Q(score=cursor_score, last_seen__gt=cursor.last_seen)
            | Q(
                score=cursor_score,
                last_seen=cursor.last_seen,
                id__gt=cursor.pk,
            )
        )
        .order_by("score", "last_seen", "id")
        .first()
    )


#: Accepted ``status`` filter values for the backlog, keyed to the note status
#: they surface.  ``open`` also covers places with no note yet; ``all`` shows
#: every status.
BACKLOG_STATUS_FILTERS = ("all", "open", "maybe", "rejected", "added")

#: Accepted ``sort`` values for the backlog, mapped to their order_by.
BACKLOG_SORTS: dict[str, tuple[Any, ...]] = {
    "threads": (
        "-subject_threads",
        "-subject_subreddits",
        F("population").desc(nulls_last=True),
        "name",
        "pk",
    ),
    "subreddits": (
        "-subject_subreddits",
        "-subject_threads",
        F("population").desc(nulls_last=True),
        "name",
        "pk",
    ),
    "population": (
        F("population").desc(nulls_last=True),
        "-subject_threads",
        "name",
        "pk",
    ),
    "recency": ("-latest_seen", "-subject_threads", "name", "pk"),
}

#: Statuses hidden from the default backlog view once a place is judged.
_BACKLOG_HIDDEN_STATUSES = (PlaceNote.Status.ADDED, PlaceNote.Status.REJECTED)


def _latest_note_status() -> Subquery:
    """Return a subquery yielding each place's most recent note status."""
    latest = (
        PlaceNote.objects.filter(place=OuterRef("pk"))
        .order_by("-created")
        .values("status")[:1]
    )
    return Subquery(latest)


def backlog_queryset(
    *, status: str | None = None, sort: str | None = None
) -> QuerySet[Place]:
    """Return uncovered places ranked by recent subject-thread activity.

    ``status`` filters by the place's most recent note status: ``all`` shows
    every place, ``open`` also includes places with no note, and a specific
    status (``maybe``/``rejected``/``added``) shows only places whose latest
    note has it.  The default hides places already judged ``added`` or
    ``rejected``.  ``sort`` chooses one of :data:`BACKLOG_SORTS`; an unknown
    value falls back to the default thread ranking.
    """
    cutoff = timezone.now() - timedelta(days=BACKLOG_DAYS)
    prefix = "thread_places__thread__subreddit"

    active_thread = (
        Q(thread_places__role=ThreadPlace.Role.SUBJECT)
        & Q(thread_places__thread__last_seen__gte=cutoff)
        & (
            Q(thread_places__thread__subreddit__isnull=True)
            | Q(thread_places__thread__subreddit__muted=False)
        )
        & country_allowed_q(prefix)
    )

    queryset = (
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
            latest_seen=Max("thread_places__thread__last_seen", filter=active_thread),
            latest_note_status=_latest_note_status(),
        )
        .filter(subject_threads__gt=0)
    )

    if status in BACKLOG_STATUS_FILTERS and status not in (None, "all"):
        if status == PlaceNote.Status.OPEN:
            queryset = queryset.filter(
                Q(latest_note_status=PlaceNote.Status.OPEN)
                | Q(latest_note_status__isnull=True)
            )
        else:
            queryset = queryset.filter(latest_note_status=status)
    elif status != "all":
        # Default view: hide places already judged, but keep places with no
        # note at all.  A bare ``exclude(...__in=...)`` would drop NULL rows,
        # because SQL ``NOT (NULL IN (...))`` is NULL, not true.
        queryset = queryset.filter(
            Q(latest_note_status__isnull=True)
            | ~Q(latest_note_status__in=_BACKLOG_HIDDEN_STATUSES)
        )

    order = BACKLOG_SORTS.get(sort or "", BACKLOG_SORTS["threads"])
    return queryset.order_by(*order)


def backlog_thread_count(*, status: str | None = None, sort: str | None = None) -> int:
    """Return the distinct threads behind the current backlog places.

    A thread naming several uncovered places still counts once.  ``status`` and
    ``sort`` scope the counted places to the same view as
    :func:`backlog_queryset`.
    """
    cutoff = timezone.now() - timedelta(days=BACKLOG_DAYS)
    backlog_places = backlog_queryset(status=status, sort=sort).values("pk")
    return (
        Thread.objects.filter(
            thread_places__role=ThreadPlace.Role.SUBJECT,
            thread_places__place__in=backlog_places,
            last_seen__gte=cutoff,
        )
        .filter(Q(subreddit__isnull=True) | Q(subreddit__muted=False))
        .filter(country_allowed_q("subreddit"))
        .distinct()
        .count()
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
