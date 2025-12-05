from django.urls import path

from . import views

app_name = "clip"

urlpatterns = [
    path("", views.ClipView.as_view(), name="clip"),
    path("fetch-page/", views.FetchPageView.as_view(), name="fetch-page"),
    path("save-page/", views.SavePageView.as_view(), name="save-page"),
]
