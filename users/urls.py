from django.urls import path

from . import views

app_name = "users"

urlpatterns = [
    path("datasette-auth/", views.datasette_auth, name="datasette_auth"),
]
