from django.urls import path

from . import internal_views, views

app_name = "apikeys"

urlpatterns = [
    path("", views.APIKeyListView.as_view(), name="list"),
    path("create/", views.APIKeyCreateView.as_view(), name="create"),
    path("<uuid:pk>/revoke/", views.APIKeyRevokeView.as_view(), name="revoke"),
    path("<uuid:pk>/delete/", views.APIKeyDeleteView.as_view(), name="delete"),
    path("download/", views.APIKeyDownloadView.as_view(), name="download"),
]

# Internal API endpoints
internal_urlpatterns = [
    path("validate-key", internal_views.ValidateKeyView.as_view(), name="validate-key"),
]
