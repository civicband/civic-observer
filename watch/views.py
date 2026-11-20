"""Staff-only pages for triage and the uncovered-place backlog."""

from __future__ import annotations

import uuid
from typing import Any

from django.contrib.admin.views.decorators import staff_member_required
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.utils.decorators import method_decorator
from django.views.generic import View

from watch.models import MeetingHit, Thread, ThreadPlace
from watch.scoring import (
    backlog_queryset,
    backlog_thread_count,
    next_thread,
    previous_thread,
    queue_queryset,
)


def _is_htmx(request: HttpRequest) -> bool:
    """Return whether the request came from htmx."""
    return request.headers.get("HX-Request") == "true"


@method_decorator(staff_member_required, name="dispatch")
class TriageView(View):
    """Show one queued thread at a time with its places and meeting hits."""

    template_name = "watch/triage.html"
    partial_template_name = "watch/partials/triage_card.html"

    def get(self, request: HttpRequest) -> HttpResponse:
        thread = self._selected_thread(request)

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

        context = {
            "thread": thread,
            "queue_count": queue_queryset().count(),
            "meeting_hits_by_place": meeting_hits_by_place,
            "subject_places": subject_places,
            "mentioned_places": mentioned_places,
            "previous_thread": previous_thread(thread.pk) if thread else None,
            "next_thread": next_thread(thread.pk) if thread else None,
        }
        template_name = (
            self.partial_template_name if _is_htmx(request) else self.template_name
        )
        return render(request, template_name, context)

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
