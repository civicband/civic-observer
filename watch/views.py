"""Staff-only pages for triage, its actions, and the place backlog."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from django.contrib.admin.views.decorators import staff_member_required
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.generic import View

from watch.forms import PlaceCorrectionForm, TriageActionForm
from watch.lookup import refresh_thread_hits
from watch.models import (
    MeetingHit,
    MutedCountry,
    Place,
    PlaceAlias,
    Subreddit,
    Thread,
    ThreadPlace,
)
from watch.normalize import normalize_key
from watch.scoring import (
    backlog_queryset,
    backlog_thread_count,
    next_thread,
    previous_thread,
    queue_queryset,
    thread_country,
)

#: Partial swapped into ``#triage-card`` after an action or correction.
TRIAGE_CARD_TEMPLATE = "watch/partials/triage_card.html"


def _is_htmx(request: HttpRequest) -> bool:
    """Return whether the request came from htmx."""
    return request.headers.get("HX-Request") == "true"


def _triage_context(thread: Thread | None) -> dict[str, Any]:
    """Return the template context for one triage card."""
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

    subject_places = [
        place for place in thread_places if place.role == ThreadPlace.Role.SUBJECT
    ]
    mentioned_places = [
        place for place in thread_places if place.role == ThreadPlace.Role.MENTIONED
    ]
    for place in thread_places:
        # Convenience for templates: hits for this place without a lookup tag.
        place.meeting_hits = meeting_hits_by_place.get(place.place_id, [])  # type: ignore[attr-defined]

    return {
        "thread": thread,
        "queue_count": queue_queryset().count(),
        "meeting_hits_by_place": meeting_hits_by_place,
        "subject_places": subject_places,
        "mentioned_places": mentioned_places,
        "previous_thread": previous_thread(thread.pk) if thread else None,
        "next_thread": next_thread(thread.pk) if thread else None,
    }


def _after_action(request: HttpRequest, thread: Thread) -> HttpResponse:
    """Render the next card for htmx, or return to the queue for a plain form."""
    if _is_htmx(request):
        return render(
            request,
            TRIAGE_CARD_TEMPLATE,
            _triage_context(next_thread(thread.pk)),
        )
    return redirect("watch:triage")


@method_decorator(staff_member_required, name="dispatch")
class TriageView(View):
    """Show one queued thread at a time with its places and meeting hits."""

    template_name = "watch/triage.html"

    def get(self, request: HttpRequest) -> HttpResponse:
        thread = self._selected_thread(request)
        template_name = (
            TRIAGE_CARD_TEMPLATE if _is_htmx(request) else self.template_name
        )
        return render(request, template_name, _triage_context(thread))

    def _selected_thread(self, request: HttpRequest) -> Thread | None:
        """Return ``?thread=<uuid>``, or the head of the queue."""
        thread_id = request.GET.get("thread")
        if not thread_id:
            return next_thread()

        try:
            thread_uuid = uuid.UUID(thread_id)
        except (AttributeError, TypeError, ValueError) as exc:
            raise Http404("Unknown thread") from exc
        return get_object_or_404(Thread, pk=thread_uuid)


@method_decorator(staff_member_required, name="dispatch")
class TriageActionView(View):
    """Apply one triage action to a thread and advance to the next card."""

    def post(self, request: HttpRequest, thread_id: uuid.UUID) -> HttpResponse:
        thread = get_object_or_404(Thread, pk=thread_id)
        form = TriageActionForm(request.POST)
        if not form.is_valid():
            return HttpResponseBadRequest("Invalid triage action")

        error = self._apply(thread, form.cleaned_data)
        if error is not None:
            return HttpResponseBadRequest(error)
        return _after_action(request, thread)

    def _apply(self, thread: Thread, data: dict[str, Any]) -> str | None:
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
            thread.save(update_fields=["status", "snoozed_until", "modified"])
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
            MutedCountry.objects.get_or_create(code=country)
        elif action == TriageActionForm.Action.PROMOTE:
            place_id = data.get("place_id")
            if place_id is None:
                return "A place is required to promote"
            place = get_object_or_404(Place, pk=place_id)
            ThreadPlace.objects.update_or_create(
                thread=thread,
                place=place,
                defaults={
                    "role": ThreadPlace.Role.SUBJECT,
                    "confidence": ThreadPlace.Confidence.HIGH,
                    "source": ThreadPlace.Source.MANUAL,
                },
            )
        elif action == TriageActionForm.Action.EDIT_QUERY:
            return self._edit_query(thread, data)

        return None

    def _set_status(self, thread: Thread, status: str) -> None:
        """Set a terminal status and drop any stale snooze timestamp."""
        thread.status = status
        thread.snoozed_until = None
        thread.save(update_fields=["status", "snoozed_until", "modified"])

    def _edit_query(self, thread: Thread, data: dict[str, Any]) -> str | None:
        """Persist a query override and refresh cached meeting hits."""
        query = (data.get("query") or "").strip()
        if not query:
            return "A query is required"

        thread_place = self._query_thread_place(thread, data.get("place_id"))
        if thread_place is None:
            return "A valid place is required"

        thread_place.query_override = query
        thread_place.save(update_fields=["query_override", "modified"])
        refresh_thread_hits(thread)
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
class PlaceCorrectionView(View):
    """Record a manual place assignment, subreddit label, and optional alias."""

    def post(self, request: HttpRequest, thread_id: uuid.UUID) -> HttpResponse:
        thread = get_object_or_404(Thread, pk=thread_id)
        form = PlaceCorrectionForm(request.POST)
        if not form.is_valid():
            return HttpResponseBadRequest("Invalid place correction")

        place = get_object_or_404(Place, pk=form.cleaned_data["place_id"])
        role = form.cleaned_data.get("role") or ThreadPlace.Role.SUBJECT
        ThreadPlace.objects.update_or_create(
            thread=thread,
            place=place,
            defaults={
                "role": role,
                "confidence": ThreadPlace.Confidence.HIGH,
                "source": ThreadPlace.Source.MANUAL,
            },
        )
        self._update_subreddit_label(thread, place)

        matched_text = (form.cleaned_data.get("matched_text") or "").strip()
        alias_key = normalize_key(matched_text)
        if alias_key:
            PlaceAlias.objects.update_or_create(
                place=place,
                key=alias_key,
                kind=PlaceAlias.Kind.NICKNAME,
                defaults={"source": PlaceAlias.Source.MANUAL},
            )

        return _after_action(request, thread)

    def _update_subreddit_label(self, thread: Thread, place: Place) -> None:
        """Point a non-manual subreddit label at the corrected place."""
        subreddit = thread.subreddit
        if subreddit is None or subreddit.source == Subreddit.Source.MANUAL:
            return
        subreddit.place = place
        subreddit.source = Subreddit.Source.MANUAL
        subreddit.reviewed_at = timezone.now()
        subreddit.save(update_fields=["place", "source", "reviewed_at", "modified"])


@method_decorator(staff_member_required, name="dispatch")
class BacklogView(View):
    """List uncovered places ranked by recent subject-thread activity."""

    template_name = "watch/backlog.html"
    partial_template_name = "watch/partials/backlog_rows.html"

    def get(self, request: HttpRequest) -> HttpResponse:
        places = list(backlog_queryset())
        context: dict[str, Any] = {
            "places": places,
            "total_threads": backlog_thread_count(),
        }
        template_name = (
            self.partial_template_name if _is_htmx(request) else self.template_name
        )
        return render(request, template_name, context)
