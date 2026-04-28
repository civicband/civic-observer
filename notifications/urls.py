from django.urls import path

from . import views
from .digest_views import (
    DigestSubscriptionCreateView,
    DigestSubscriptionDeleteView,
    DigestSubscriptionListView,
)

app_name = "notifications"

urlpatterns = [
    path("channels/", views.ChannelListView.as_view(), name="channel-list"),
    path("channels/create/", views.ChannelCreateView.as_view(), name="channel-create"),
    path(
        "channels/<uuid:pk>/delete/",
        views.ChannelDeleteView.as_view(),
        name="channel-delete",
    ),
    path("digests/", DigestSubscriptionListView.as_view(), name="digest-list"),
    path(
        "digests/create/",
        DigestSubscriptionCreateView.as_view(),
        name="digest-create",
    ),
    path(
        "digests/<uuid:pk>/delete/",
        DigestSubscriptionDeleteView.as_view(),
        name="digest-delete",
    ),
]
