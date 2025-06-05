from django.urls import path
from neapolitan.views import Role

from .views import SavedSearchCRUDView

app_name = "searches"

urlpatterns = [
    path("", SavedSearchCRUDView.as_view(role=Role.LIST), name="savedsearch-list"),
    path(
        "create/",
        SavedSearchCRUDView.as_view(role=Role.CREATE),
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
]
