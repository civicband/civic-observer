"""Tests for operator place labels on threads."""

import pytest
from django.contrib import admin
from django.db import IntegrityError, transaction
from django.urls import reverse

from tests.factories import PlaceFactory, ThreadFactory
from watch.admin import HasLabelFilter, ThreadLabelInline
from watch.models import Thread, ThreadLabel


@pytest.mark.django_db
def test_thread_label_unique_per_place():
    thread = ThreadFactory()
    place = PlaceFactory()
    ThreadLabel.objects.create(thread=thread, place=place, is_subject=True)
    with pytest.raises(IntegrityError), transaction.atomic():
        ThreadLabel.objects.create(thread=thread, place=place, is_subject=False)


@pytest.mark.django_db
def test_thread_label_allows_one_no_place_row():
    thread = ThreadFactory()
    ThreadLabel.objects.create(thread=thread, place=None, is_useful=False)
    with pytest.raises(IntegrityError), transaction.atomic():
        ThreadLabel.objects.create(thread=thread, place=None, is_useful=False)


def test_thread_label_admin_configuration():
    thread_admin = admin.site._registry[Thread]

    assert ThreadLabel in admin.site._registry
    assert any(
        isinstance(inline, type) and issubclass(inline, ThreadLabelInline)
        for inline in thread_admin.inlines
    )
    assert HasLabelFilter in thread_admin.list_filter


@pytest.mark.django_db
def test_thread_admin_has_label_filter(admin_client):
    labelled = ThreadFactory()
    ThreadLabel.objects.create(thread=labelled, place=PlaceFactory())
    ThreadFactory()

    response = admin_client.get(
        reverse("admin:watch_thread_changelist"), {"has_label": "yes"}
    )

    assert response.status_code == 200
    assert [thread.pk for thread in response.context["cl"].queryset] == [labelled.pk]
