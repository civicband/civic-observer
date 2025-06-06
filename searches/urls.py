from django.urls import path
from neapolitan.views import Role

from .views import (
    SavedSearchCreateView,
    SavedSearchCRUDView,
    saved_search_email_preview,
)

app_name = "searches"

urlpatterns = [
    path("", SavedSearchCRUDView.as_view(role=Role.LIST), name="savedsearch-list"),
    path(
        "create/",
        SavedSearchCreateView.as_view(),
        name="savedsearch-create",
    ),
    path(
        "<uuid:pk>/",
        SavedSearchCRUDView.as_view(role=Role.DETAIL),
        name="savedsearch-detail",
    ),
    path(
        "<uuid:pk>/update/",
        SavedSearchCRUDView.as_view(role=Role.UPDATE),
        name="savedsearch-update",
    ),
    path(
        "<uuid:pk>/delete/",
        SavedSearchCRUDView.as_view(role=Role.DELETE),
        name="savedsearch-delete",
    ),
    # Email preview URLs (staff only)
    path(
        "<uuid:pk>/email-preview/",
        saved_search_email_preview,
        name="savedsearch-email-preview",
    ),
    path(
        "<uuid:pk>/email-preview/<str:format>/",
        saved_search_email_preview,
        name="savedsearch-email-preview-format",
    ),
]
