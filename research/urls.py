from django.urls import path

from . import views

app_name = "research"

urlpatterns = [
    path("", views.ProjectListView.as_view(), name="project-list"),
    path("<uuid:project>/", views.ProjectDetailView.as_view(), name="project-detail"),
    path(
        "<uuid:project>/rules/",
        views.IgnoreRuleListView.as_view(),
        name="rule-list",
    ),
    path(
        "<uuid:project>/rules/new/",
        views.IgnoreRuleCreateView.as_view(),
        name="rule-create",
    ),
    path(
        "<uuid:project>/rules/<uuid:rule>/toggle/",
        views.IgnoreRuleToggleView.as_view(),
        name="rule-toggle",
    ),
    path(
        "<uuid:project>/rules/<uuid:rule>/delete/",
        views.IgnoreRuleDeleteView.as_view(),
        name="rule-delete",
    ),
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
        "<uuid:project>/sets/<uuid:set>/freeze/",
        views.FreezeLensView.as_view(),
        name="set-freeze",
    ),
    path(
        "<uuid:project>/sets/<uuid:set>/new-matches/",
        views.NewMatchesView.as_view(),
        name="set-new-matches",
    ),
    path(
        "<uuid:project>/sets/<uuid:set>/items/<int:item>/disposition/",
        views.DispositionView.as_view(),
        name="item-disposition",
    ),
    path(
        "<uuid:project>/sets/<uuid:set>/review/",
        views.ReviewView.as_view(),
        name="set-review",
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
