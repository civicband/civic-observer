"""Tests for the ingest health banner on the admin index."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth.models import AnonymousUser
from django.template import RequestContext, Template
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

from tests.factories import IngestRunFactory, UserFactory


@pytest.mark.django_db
def test_admin_index_shows_last_success(admin_client):
    IngestRunFactory(finished_at=timezone.now(), new=4, error="")
    response = admin_client.get(reverse("admin:index"))
    assert b"Last successful ingest" in response.content


@pytest.mark.django_db
def test_admin_index_shows_hours_since_success(admin_client):
    IngestRunFactory(finished_at=timezone.now() - timedelta(hours=2), error="")

    response = admin_client.get(reverse("admin:index"))

    assert b"Hours since last success" in response.content


def test_watch_health_tag_renders_nothing_for_non_staff():
    request = RequestFactory().get("/admin/")
    request.user = AnonymousUser()
    template = Template("{% load watch_health %}{% watch_health %}")

    rendered = template.render(RequestContext(request))

    assert rendered.strip() == ""


@pytest.mark.django_db
def test_watch_health_tag_renders_nothing_for_logged_in_non_staff():
    request = RequestFactory().get("/admin/")
    request.user = UserFactory(is_staff=False)
    template = Template("{% load watch_health %}{% watch_health %}")

    rendered = template.render(RequestContext(request))

    assert rendered.strip() == ""
