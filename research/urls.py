from django.urls import path

from . import views

app_name = "research"

urlpatterns = [
    path("", views.ProjectListView.as_view(), name="project-list"),
    path("<uuid:project>/", views.ProjectDetailView.as_view(), name="project-detail"),
    path(
        "<uuid:project>/sets/new/",
        views.SetCreateView.as_view(),
        name="set-create",
    ),
    path(
        "<uuid:project>/sets/<uuid:set>/",
        views.SetDetailView.as_view(),
        name="set-detail",
    ),
    path(
        "<uuid:project>/sets/<uuid:set>/status/",
        views.SetStatusView.as_view(),
        name="set-status",
    ),
    path(
        "<uuid:project>/sets/<uuid:set>/items/<int:item>/disposition/",
        views.DispositionView.as_view(),
        name="item-disposition",
    ),
    path(
        "<uuid:project>/sets/<uuid:set>/bulk/",
        views.BulkActionView.as_view(),
        name="set-bulk",
    ),
    path(
        "<uuid:project>/sets/<uuid:set>/events/<int:event>/undo/",
        views.UndoView.as_view(),
        name="event-undo",
    ),
]
