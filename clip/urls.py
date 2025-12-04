from django.urls import path

from . import views

app_name = "clip"

urlpatterns = [
    path("", views.ClipView.as_view(), name="clip"),
]
