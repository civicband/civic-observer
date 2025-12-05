"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.contrib import admin
from django.urls import URLPattern, URLResolver, include, path

from apikeys.urls import internal_urlpatterns as apikeys_internal
from users.views import datasette_auth, login_view

from . import views

urlpatterns: list[URLPattern | URLResolver] = [
    path("", views.homepage, name="homepage"),
    path("admin/", admin.site.urls),
    path("api-keys/", include("apikeys.urls")),
    path("api/v1/", include((apikeys_internal, "apikeys_internal"))),
    path("auth/", include("stagedoor.urls", namespace="stagedoor")),
    path("clip/", include("clip.urls")),
    path("datasette-auth/", datasette_auth, name="datasette_auth"),
    path("django-rq/", include("django_rq.urls")),
    path("health/", views.health_check, name="health_check"),
    path("login/", login_view, name="login"),
    path("meetings/", include("meetings.urls")),
    path("munis/", include("municipalities.urls")),
    path("notebooks/", include("notebooks.urls")),
    path("notifications/", include("notifications.urls")),
    path("searches/", include("searches.urls")),
    path("users/", include("users.urls")),
]
