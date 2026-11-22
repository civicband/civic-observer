"""URL routing for the staff-only watch pages."""

from django.urls import path

from watch.views import (
    BacklogNoteView,
    BacklogView,
    PlaceCorrectionView,
    PlaceRejectionView,
    PlaceSearchView,
    TriageActionView,
    TriageView,
)

app_name = "watch"

urlpatterns = [
    path("triage/", TriageView.as_view(), name="triage"),
    path(
        "triage/place-search/",
        PlaceSearchView.as_view(),
        name="place-search",
    ),
    path(
        "triage/<uuid:thread_id>/action/",
        TriageActionView.as_view(),
        name="action",
    ),
    path(
        "triage/<uuid:thread_id>/place/",
        PlaceCorrectionView.as_view(),
        name="place-correct",
    ),
    path(
        "triage/<uuid:thread_id>/place/reject/",
        PlaceRejectionView.as_view(),
        name="place-reject",
    ),
    path("backlog/", BacklogView.as_view(), name="backlog"),
    path(
        "backlog/<uuid:place_id>/note/",
        BacklogNoteView.as_view(),
        name="backlog-note",
    ),
]
