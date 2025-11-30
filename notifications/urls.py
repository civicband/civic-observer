from django.urls import path

from . import views

app_name = "notifications"

urlpatterns = [
    path("channels/", views.ChannelListView.as_view(), name="channel-list"),
    path("channels/create/", views.ChannelCreateView.as_view(), name="channel-create"),
    path(
        "channels/<uuid:pk>/delete/",
        views.ChannelDeleteView.as_view(),
        name="channel-delete",
    ),
]
