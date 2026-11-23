"""Staff-only pages for triage, its actions, and the place backlog."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from datetime import timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

from django.contrib.admin.views.decorators import staff_member_required
from django.db import transaction
from django.db.models import (
    Case,
    F,
    IntegerField,
    Prefetch,
    Q,
    QuerySet,
    Value,
    When,
)
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.utils.http import urlencode
from django.views.generic import View

from watch.forms import (
    PlaceCorrectionForm,
    PlaceNoteForm,
    PlaceRejectionForm,
    TriageActionForm,
    TriageBulkActionForm,
)
from watch.ingest import enqueue_enrichment
from watch.models import (
    MeetingHit,
    MutedCountry,
    Place,
    PlaceAlias,
    PlaceNote,
    Subreddit,
    Thread,
    ThreadLabel,
    ThreadPlace,
    dismiss_threads,
)
from watch.normalize import normalize_key
from watch.scoring import (
    BACKLOG_DAYS,
    BACKLOG_SORTS,
    BACKLOG_STATUS_FILTERS,
    QUEUE_AGE_CHOICES,
    QUEUE_AGE_OPTIONS,
    QUEUE_COVERED_CHOICES,
    QUEUE_SORTS,
    QUEUE_US_CHOICES,
    backlog_queryset,
    backlog_thread_count,
    country_allowed_q,
    filtered_queue_queryset,
    next_thread,
    previous_thread,
    thread_country,
)

#: Detail pane partial, swapped into ``#triage-detail``.
TRIAGE_CARD_TEMPLATE = "watch/partials/triage_card.html"

#: Queue list partial, swapped into ``#triage-list``.
TRIAGE_LIST_TEMPLATE = "watch/partials/triage_list.html"

#: Bulk action response: refreshed queue plus an out-of-band detail refresh.
TRIAGE_BULK_RESPONSE_TEMPLATE = "watch/partials/triage_bulk_response.html"

#: Maximum number of threads a single bulk close request may update.
BULK_CLOSE_LIMIT = 5000

#: Partial whose rows are swapped after a backlog note is saved.
BACKLOG_ROWS_TEMPLATE = "watch/partials/backlog_rows.html"

#: Place typeahead results partial.
PLACE_SEARCH_TEMPLATE = "watch/partials/place_search_results.html"

#: Threads shown per backlog row as a taste of what named the place.
SAMPLE_THREAD_LIMIT = 3

#: Maximum number of places returned by the typeahead.
PLACE_SEARCH_LIMIT = 8

#: Actions that remove the thread from the queue and move the operator on.
ADVANCING_ACTIONS = frozenset(
    {
        TriageActionForm.Action.REPLIED,
        TriageActionForm.Action.DISMISS,
        TriageActionForm.Action.BACKLOG,
        TriageActionForm.Action.SNOOZE,
    }
)

#: Triage list filters read from the query string, with their allowed values.
_QUEUE_FILTER_KEYS = {
    "covered": QUEUE_COVERED_CHOICES,
    "kind": None,
    "us": QUEUE_US_CHOICES,
    "age": QUEUE_AGE_CHOICES,
    "sort": tuple(QUEUE_SORTS),
    "q": None,
}

#: Backlog filters read from the query string.
_BACKLOG_FILTER_KEYS = {
    "status": BACKLOG_STATUS_FILTERS,
    "sort": tuple(BACKLOG_SORTS),
}

logger = logging.getLogger(__name__)


def _is_htmx(request: HttpRequest) -> bool:
    """Return whether the request came from htmx."""
    return request.headers.get("HX-Request") == "true"


def _clean_params(
    request: HttpRequest, spec: Mapping[str, tuple[str, ...] | None]
) -> dict[str, str]:
    """Return the accepted query-string values for ``spec``.

    Unknown values for a key with an allowed set are dropped, so a stale
    bookmark renders a sensible default rather than an empty page.  Keys with
    ``None`` are passed through after stripping.
    """
    cleaned: dict[str, str] = {}
    for key, allowed in spec.items():
        value = (request.GET.get(key) or "").strip()
        if not value:
            continue
        if allowed is not None and value not in allowed:
            continue
        cleaned[key] = value
    return cleaned


def _queue_filters(request: HttpRequest) -> dict[str, str]:
    """Return the triage list filters from the query string."""
    return _clean_params(request, _QUEUE_FILTER_KEYS)


def _backlog_filters(request: HttpRequest) -> dict[str, str]:
    """Return the backlog filters from the query string."""
    return _clean_params(request, _BACKLOG_FILTER_KEYS)


def _triage_url(filters: Mapping[str, str]) -> str:
    """Return the triage URL for ``filters``."""
    query = urlencode(filters)
    url = reverse("watch:triage")
    return f"{url}?{query}" if query else url


def _request_query_value(request: HttpRequest, key: str) -> str | None:
    """Read a query value from the current page URL, preferring HTMX's URL."""
    current_url = request.headers.get("HX-Current-URL")
    if current_url:
        try:
            query = urlparse(current_url).query
        except ValueError:
            return None
        values = parse_qs(query).get(key)
        return values[0] if values else None
    return request.GET.get(key)


def _bulk_detail_thread(
    request: HttpRequest, queryset: QuerySet[Thread]
) -> Thread | None:
    """Return the thread still selected by the page URL, else the queue head."""
    raw_thread_id = _request_query_value(request, "thread")
    if raw_thread_id:
        try:
            thread_id = uuid.UUID(raw_thread_id)
        except (AttributeError, TypeError, ValueError):
            thread_id = None
        if thread_id is not None:
            selected = queryset.filter(pk=thread_id).first()
            if selected is not None:
                return selected
    return queryset.first()


def _sample_thread_places():
    """Return the subject thread links shown as samples in a backlog row.

    Mirrors the backlog's activity window, subreddit mute, and country mute
    rules, and joins the thread and subreddit so rendering a row costs no
    extra queries.
    """
    cutoff = timezone.now() - timedelta(days=BACKLOG_DAYS)
    return (
        ThreadPlace.objects.filter(
            role=ThreadPlace.Role.SUBJECT,
            thread__last_seen__gte=cutoff,
        )
        .filter(Q(thread__subreddit__isnull=True) | Q(thread__subreddit__muted=False))
        .filter(country_allowed_q("thread__subreddit"))
        .select_related("thread", "thread__subreddit")
        .order_by("-thread__last_seen", "-thread__id")
    )


def _backlog_place_list(
    place_id: uuid.UUID | None = None,
    *,
    status: str | None = None,
    sort: str | None = None,
) -> list[Place]:
    """Return ranked backlog places with sampled threads and notes prefetched."""
    queryset = backlog_queryset(status=status, sort=sort).prefetch_related(
        Prefetch(
            "thread_places",
            queryset=_sample_thread_places(),
            to_attr="sample_threads",
        ),
        Prefetch("notes", queryset=PlaceNote.objects.order_by("-created")),
    )
    if place_id is not None:
        queryset = queryset.filter(pk=place_id)

    places = list(queryset.select_related("muni"))
    for place in places:
        # Prefetched in full, then trimmed: a sliced prefetch queryset is not
        # portable across all supported Django database backends.
        place.sample_threads = place.sample_threads[  # type: ignore[attr-defined]
            :SAMPLE_THREAD_LIMIT
        ]
    return places


def _backlog_context(
    places: list[Place],
    *,
    include_total: bool = False,
    status: str | None = None,
    sort: str | None = None,
) -> dict[str, Any]:
    """Return the template context shared by the page, rows, and note view."""
    context: dict[str, Any] = {
        "places": places,
        "note_statuses": PlaceNote.Status.choices,
        "status": status or "all",
        "sort": sort or "threads",
        "status_filters": BACKLOG_STATUS_FILTERS,
        "sort_options": tuple(BACKLOG_SORTS),
    }
    if include_total:
        context["total_threads"] = backlog_thread_count(status=status, sort=sort)
    return context


def _queue_rows(filters: dict[str, str]) -> list[Thread]:
    """Return the ranked, filtered queue rows for the list pane."""
    return list(
        filtered_queue_queryset(**filters).select_related(
            "subreddit", "subreddit__place"
        )
    )


def _triage_context(
    thread: Thread | None, *, filters: dict[str, str] | None = None
) -> dict[str, Any]:
    """Return the template context for the triage page and its partials."""
    filters = filters or {}
    rows = _queue_rows(filters)
    queue_queryset_for_nav = filtered_queue_queryset(**filters)

    thread_places: list[ThreadPlace] = []
    meeting_hits_by_place: dict[uuid.UUID, list[MeetingHit]] = {}
    if thread is not None:
        thread_places = list(
            thread.thread_places.select_related("place", "place__muni").order_by(
                "-role", "place__name"
            )
        )
        for hit in MeetingHit.objects.filter(thread=thread).select_related("place"):
            meeting_hits_by_place.setdefault(hit.place_id, []).append(hit)

    # Operator judgements ("this place is wrong") hide a suggestion without
    # deleting the extraction, so the review decision is visible and auditable.
    rejected_place_ids: set[uuid.UUID] = set()
    if thread is not None:
        rejected_place_ids = set(
            thread.labels.filter(place__isnull=False, is_useful=False).values_list(
                "place_id", flat=True
            )
        )

    open_places = [
        place for place in thread_places if place.place_id not in rejected_place_ids
    ]
    rejected_places = [
        place for place in thread_places if place.place_id in rejected_place_ids
    ]
    subject_places = [
        place for place in open_places if place.role == ThreadPlace.Role.SUBJECT
    ]
    mentioned_places = [
        place for place in open_places if place.role == ThreadPlace.Role.MENTIONED
    ]
    for place in thread_places:
        # Convenience for templates: hits for this place without a lookup tag.
        place.meeting_hits = meeting_hits_by_place.get(place.place_id, [])  # type: ignore[attr-defined]
        place.is_rejected = place.place_id in rejected_place_ids  # type: ignore[attr-defined]

    return {
        "thread": thread,
        "rows": rows,
        "filters": filters,
        "age_options": QUEUE_AGE_OPTIONS,
        "queue_count": queue_queryset_for_nav.count(),
        "thread_country": thread_country(thread) if thread else None,
        "meeting_hits_by_place": meeting_hits_by_place,
        "subject_places": subject_places,
        "mentioned_places": mentioned_places,
        "rejected_places": rejected_places,
        "also_posted_in": (
            thread.duplicates.select_related("subreddit").order_by("first_seen", "pk")
            if thread is not None
            else []
        ),
        "previous_thread": (
            previous_thread(thread.pk, queryset=queue_queryset_for_nav)
            if thread
            else None
        ),
        "next_thread": (
            next_thread(thread.pk, queryset=queue_queryset_for_nav) if thread else None
        ),
    }


def _after_action(
    request: HttpRequest, thread: Thread, *, advance: bool
) -> HttpResponse:
    """Render the htmx detail card, or return to the queue for a plain form.

    Actions that take the thread out of the queue advance to the next thread;
    actions that keep it queued re-render the current thread so the operator
    can see the change.  The response also carries an out-of-band refresh of
    the list pane so an acted-on row disappears without a full reload.
    """
    if not _is_htmx(request):
        return redirect("watch:triage")

    filters = _queue_filters(request)
    nav_queryset = filtered_queue_queryset(**filters)
    shown = next_thread(thread.pk, queryset=nav_queryset) if advance else thread
    context = _triage_context(shown, filters=filters)
    # Ask the card to also emit an out-of-band refresh of the list pane so the
    # acted-on row disappears (or the order updates) without a full reload.
    context["oob_list"] = True
    return render(request, TRIAGE_CARD_TEMPLATE, context)


@method_decorator(staff_member_required, name="dispatch")
class TriageView(View):
    """Show the ranked thread list beside one selected thread's detail."""

    template_name = "watch/triage.html"

    def get(self, request: HttpRequest) -> HttpResponse:
        filters = _queue_filters(request)
        thread = self._selected_thread(request, filters)
        context = _triage_context(thread, filters=filters)

        if _is_htmx(request):
            target = request.headers.get("HX-Target", "")
            if target == "triage-list":
                return render(request, TRIAGE_LIST_TEMPLATE, context)
            return render(request, TRIAGE_CARD_TEMPLATE, context)

        return render(request, self.template_name, context)

    def _selected_thread(
        self, request: HttpRequest, filters: dict[str, str]
    ) -> Thread | None:
        """Return ``?thread=<uuid>``, or the head of the filtered queue."""
        thread_id = request.GET.get("thread")
        if not thread_id:
            return filtered_queue_queryset(**filters).first()

        try:
            thread_uuid = uuid.UUID(thread_id)
        except (AttributeError, TypeError, ValueError) as exc:
            raise Http404("Unknown thread") from exc
        return get_object_or_404(Thread, pk=thread_uuid)


@method_decorator(staff_member_required, name="dispatch")
class TriageActionView(View):
    """Apply one triage action to a thread and render the appropriate card."""

    def post(self, request: HttpRequest, thread_id: uuid.UUID) -> HttpResponse:
        thread = get_object_or_404(Thread, pk=thread_id)
        form = TriageActionForm(request.POST)
        if not form.is_valid():
            return HttpResponseBadRequest("Invalid triage action")

        error = self._apply(thread, form.cleaned_data, request)
        if error is not None:
            return HttpResponseBadRequest(error)
        advance = form.cleaned_data["action"] in ADVANCING_ACTIONS
        return _after_action(request, thread, advance=advance)

    def _apply(
        self, thread: Thread, data: dict[str, Any], request: HttpRequest
    ) -> str | None:
        """Apply ``data['action']`` and return an error message, if any."""
        action = data["action"]

        if action == TriageActionForm.Action.REPLIED:
            self._set_status(thread, Thread.Status.REPLIED)
        elif action == TriageActionForm.Action.DISMISS:
            self._set_status(thread, Thread.Status.DISMISSED)
        elif action == TriageActionForm.Action.BACKLOG:
            self._set_status(thread, Thread.Status.BACKLOG)
        elif action == TriageActionForm.Action.SNOOZE:
            days = data.get("days") or 7
            thread.status = Thread.Status.SNOOZED
            thread.snoozed_until = timezone.now() + timedelta(days=days)
            thread.dismissed_at = None
            thread.save(
                update_fields=[
                    "status",
                    "snoozed_until",
                    "dismissed_at",
                    "modified",
                ]
            )
        elif action == TriageActionForm.Action.MUTE_SUBREDDIT:
            subreddit = thread.subreddit
            if subreddit is None:
                return "Thread has no subreddit"
            subreddit.muted = True
            subreddit.save(update_fields=["muted", "modified"])
        elif action == TriageActionForm.Action.MUTE_COUNTRY:
            country = thread_country(thread)
            if not country:
                return "Thread has no country"
            if country == "US":
                return "The United States cannot be muted"
            MutedCountry.objects.get_or_create(code=country)
        elif action == TriageActionForm.Action.PROMOTE:
            place_id = data.get("place_id")
            if place_id is None:
                return "A place is required to promote"
            thread_place = ThreadPlace.objects.filter(
                thread=thread, place_id=place_id
            ).first()
            if thread_place is None:
                return "A valid place is required"
            thread_place.role = ThreadPlace.Role.SUBJECT
            thread_place.confidence = ThreadPlace.Confidence.HIGH
            thread_place.source = ThreadPlace.Source.MANUAL
            thread_place.save(
                update_fields=["role", "confidence", "source", "modified"]
            )
        elif action == TriageActionForm.Action.EDIT_QUERY:
            return self._edit_query(thread, data)
        elif action == TriageActionForm.Action.NO_PLACE:
            ThreadLabel.objects.update_or_create(
                thread=thread,
                place=None,
                defaults={"is_subject": False, "created_by": request.user},
            )

        return None

    def _set_status(self, thread: Thread, status: str) -> None:
        """Set a terminal status and drop any stale snooze timestamp."""
        if status == Thread.Status.DISMISSED:
            thread.dismiss()
            return
        thread.status = status
        thread.snoozed_until = None
        thread.dismissed_at = None
        thread.save(
            update_fields=[
                "status",
                "snoozed_until",
                "dismissed_at",
                "modified",
            ]
        )

    def _edit_query(self, thread: Thread, data: dict[str, Any]) -> str | None:
        """Persist a query override and queue hit enrichment."""
        query = (data.get("query") or "").strip()
        if not query:
            return "A query is required"

        thread_place = self._query_thread_place(thread, data.get("place_id"))
        if thread_place is None:
            return "A valid place is required"

        thread_place.query_override = query
        thread_place.save(update_fields=["query_override", "modified"])
        try:
            enqueue_enrichment(str(thread.id))
        except Exception:
            # A missing or unreachable queue must not lose the saved query.
            logger.exception("Could not enqueue enrichment for thread %s", thread.id)
        return None

    def _query_thread_place(
        self, thread: Thread, place_id: uuid.UUID | None
    ) -> ThreadPlace | None:
        """Return the thread place to override, or ``None`` when ambiguous."""
        if place_id is not None:
            return ThreadPlace.objects.filter(thread=thread, place_id=place_id).first()

        subject_places = ThreadPlace.objects.filter(
            thread=thread, role=ThreadPlace.Role.SUBJECT
        )
        if subject_places.count() != 1:
            return None
        return subject_places.first()


@method_decorator(staff_member_required, name="dispatch")
class TriageBulkActionView(View):
    """Apply one bulk action to the selected threads in the triage queue."""

    def post(self, request: HttpRequest) -> HttpResponse:
        filters = _queue_filters(request)
        form = TriageBulkActionForm(request.POST)
        if not form.is_valid():
            return HttpResponseBadRequest("Invalid bulk triage action")

        if form.cleaned_data["action"] == TriageBulkActionForm.Action.CLOSE:
            selected_ids = set(form.cleaned_data["thread_ids"])
            selected_ids.update(thread.pk for thread in form.cleaned_data["thread_id"])
            if len(selected_ids) > BULK_CLOSE_LIMIT:
                return HttpResponseBadRequest("Too many threads selected")
            dismiss_threads(
                filtered_queue_queryset(**filters).filter(pk__in=selected_ids)
            )

        if not _is_htmx(request):
            return redirect(_triage_url(filters))

        shown = _bulk_detail_thread(request, filtered_queue_queryset(**filters))
        context = _triage_context(shown, filters=filters)
        return render(request, TRIAGE_BULK_RESPONSE_TEMPLATE, context)


@method_decorator(staff_member_required, name="dispatch")
class PlaceCorrectionView(View):
    """Record a manual place assignment, subreddit label, and optional alias."""

    def post(self, request: HttpRequest, thread_id: uuid.UUID) -> HttpResponse:
        thread = get_object_or_404(Thread, pk=thread_id)
        form = PlaceCorrectionForm(request.POST)
        if not form.is_valid():
            return HttpResponseBadRequest("Invalid place correction")

        place = get_object_or_404(Place, pk=form.cleaned_data["place_id"])
        role = form.cleaned_data.get("role") or ThreadPlace.Role.SUBJECT
        matched_text = (form.cleaned_data.get("matched_text") or "").strip()

        with transaction.atomic():
            ThreadPlace.objects.update_or_create(
                thread=thread,
                place=place,
                defaults={
                    "role": role,
                    "confidence": ThreadPlace.Confidence.HIGH,
                    "source": ThreadPlace.Source.MANUAL,
                },
            )
            # A manual assignment also clears a "not a match" judgement.
            ThreadLabel.objects.update_or_create(
                thread=thread,
                place=place,
                defaults={
                    "is_subject": role == ThreadPlace.Role.SUBJECT,
                    "is_useful": True,
                    "created_by": request.user,
                },
            )
            if role == ThreadPlace.Role.SUBJECT:
                self._update_subreddit_label(thread, place)

            alias_key = normalize_key(matched_text)
            if alias_key:
                PlaceAlias.objects.update_or_create(
                    place=place,
                    key=alias_key,
                    kind=PlaceAlias.Kind.NICKNAME,
                    defaults={"source": PlaceAlias.Source.MANUAL},
                )

        return _after_action(request, thread, advance=False)

    def _update_subreddit_label(self, thread: Thread, place: Place) -> None:
        """Point a non-manual subreddit label at the corrected place."""
        subreddit = thread.subreddit
        if subreddit is None or subreddit.source == Subreddit.Source.MANUAL:
            return
        subreddit.place = place
        subreddit.kind = Subreddit.Kind.PLACE
        subreddit.state = place.state
        subreddit.country = place.country or None
        subreddit.source = Subreddit.Source.MANUAL
        subreddit.reviewed_at = timezone.now()
        subreddit.save(
            update_fields=[
                "place",
                "kind",
                "state",
                "country",
                "source",
                "reviewed_at",
                "modified",
            ]
        )


@method_decorator(staff_member_required, name="dispatch")
class PlaceRejectionView(View):
    """Record that a suggested place is wrong for a thread.

    The row is kept so the extraction stays auditable; triage filters it out
    of the open suggestions and lists it under "Rejected suggestions".
    """

    def post(self, request: HttpRequest, thread_id: uuid.UUID) -> HttpResponse:
        thread = get_object_or_404(Thread, pk=thread_id)
        form = PlaceRejectionForm(request.POST)
        if not form.is_valid():
            return HttpResponseBadRequest("Invalid place rejection")

        place_id = form.cleaned_data["place_id"]
        if not ThreadPlace.objects.filter(thread=thread, place_id=place_id).exists():
            return HttpResponseBadRequest("A valid place is required")

        ThreadLabel.objects.update_or_create(
            thread=thread,
            place_id=place_id,
            defaults={
                "is_subject": False,
                "is_useful": False,
                "created_by": request.user,
            },
        )
        return _after_action(request, thread, advance=False)


@method_decorator(staff_member_required, name="dispatch")
class PlaceSearchView(View):
    """Return typeahead matches for the place-correction input."""

    def get(self, request: HttpRequest) -> HttpResponse:
        query = (request.GET.get("q") or "").strip()
        matches = self._matches(query)
        return render(
            request,
            PLACE_SEARCH_TEMPLATE,
            {"places": matches, "query": query},
        )

    def _matches(self, query: str) -> list[Place]:
        """Return places whose name or alias matches ``query``, best first."""
        if not query:
            return []
        key = normalize_key(query)
        filters = Q(name__icontains=query) | Q(aliases__key__icontains=key)
        return list(
            Place.objects.filter(filters)
            .select_related("muni")
            .annotate(
                prefix_match=Case(
                    When(name__istartswith=query, then=Value(0)),
                    default=Value(1),
                    output_field=IntegerField(),
                )
            )
            .order_by("prefix_match", F("population").desc(nulls_last=True), "name")
            .distinct()[:PLACE_SEARCH_LIMIT]
        )


@method_decorator(staff_member_required, name="dispatch")
class BacklogView(View):
    """List uncovered places ranked by recent subject-thread activity."""

    template_name = "watch/backlog.html"
    partial_template_name = BACKLOG_ROWS_TEMPLATE

    def get(self, request: HttpRequest) -> HttpResponse:
        filters = _backlog_filters(request)
        status = filters.get("status")
        sort = filters.get("sort")
        places = _backlog_place_list(status=status, sort=sort)
        partial = _is_htmx(request)
        template_name = self.partial_template_name if partial else self.template_name
        return render(
            request,
            template_name,
            _backlog_context(
                places,
                include_total=not partial,
                status=status,
                sort=sort,
            ),
        )


@method_decorator(staff_member_required, name="dispatch")
class BacklogNoteView(View):
    """Record a note about an uncovered place and refresh its backlog row."""

    def post(self, request: HttpRequest, place_id: uuid.UUID) -> HttpResponse:
        place = get_object_or_404(Place, pk=place_id, muni__isnull=True)
        form = PlaceNoteForm(request.POST)
        if not form.is_valid():
            return HttpResponseBadRequest("Invalid place note")

        note = form.save(commit=False)
        note.place = place
        note.save()

        if not _is_htmx(request):
            return redirect("watch:backlog")

        filters = _backlog_filters(request)
        status = filters.get("status")
        sort = filters.get("sort")
        places = _backlog_place_list(place.pk, status=status, sort=sort)
        if not places:
            # The note still saved; show it even if the place has no recent
            # subject threads and therefore left the ranking.
            place.subject_threads = 0  # type: ignore[attr-defined]
            place.subject_subreddits = 0  # type: ignore[attr-defined]
            place.sample_threads = []  # type: ignore[attr-defined]
            place.latest_note_status = note.status  # type: ignore[attr-defined]
            places = [place]
        return render(
            request,
            BACKLOG_ROWS_TEMPLATE,
            _backlog_context(places, status=status, sort=sort),
        )
