"""Tests for operator place labels on threads."""

import pytest
from django.contrib import admin
from django.db import IntegrityError, transaction
from django.urls import reverse

from tests.factories import PlaceFactory, ThreadFactory, UserFactory
from watch.admin import HasLabelFilter, ThreadLabelInline
from watch.models import Place, Thread, ThreadLabel


def _management_form(total: int, initial: int = 0) -> dict[str, str]:
    return {
        "labels-TOTAL_FORMS": str(total),
        "labels-INITIAL_FORMS": str(initial),
        "labels-MIN_NUM_FORMS": "0",
        "labels-MAX_NUM_FORMS": "1000",
    }


def _label_form(index: int, *, place: str = "", **fields: str) -> dict[str, str]:
    data = {
        f"labels-{index}-id": "",
        f"labels-{index}-place": place,
        f"labels-{index}-is_subject": "",
        f"labels-{index}-is_useful": "",
    }
    data.update({f"labels-{index}-{name}": value for name, value in fields.items()})
    return data


def _thread_form_data(thread: Thread) -> dict[str, str]:
    return {
        "key": thread.key,
        "title": thread.title,
        "first_seen_0": thread.first_seen.strftime("%Y-%m-%d"),
        "first_seen_1": thread.first_seen.strftime("%H:%M:%S"),
        "last_seen_0": thread.last_seen.strftime("%Y-%m-%d"),
        "last_seen_1": thread.last_seen.strftime("%H:%M:%S"),
        "status": thread.status,
        "mention_count": str(thread.mention_count),
        "notes": thread.notes,
    }


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


@pytest.mark.django_db
def test_thread_label_allows_same_place_on_different_threads():
    place = PlaceFactory()
    first = ThreadFactory()
    second = ThreadFactory()

    ThreadLabel.objects.create(thread=first, place=place)
    ThreadLabel.objects.create(thread=second, place=place)

    assert ThreadLabel.objects.filter(place=place).count() == 2


@pytest.mark.django_db
def test_thread_label_allows_different_places_on_same_thread():
    thread = ThreadFactory()

    ThreadLabel.objects.create(thread=thread, place=PlaceFactory())
    ThreadLabel.objects.create(thread=thread, place=PlaceFactory())

    assert ThreadLabel.objects.filter(thread=thread).count() == 2


@pytest.mark.django_db
def test_deleting_place_removes_its_label_and_keeps_no_place_label():
    thread = ThreadFactory()
    place = PlaceFactory()
    ThreadLabel.objects.create(thread=thread, place=place, is_subject=True)
    ThreadLabel.objects.create(thread=thread, place=None, is_useful=False)

    place.delete()

    assert not ThreadLabel.objects.filter(thread=thread, place__isnull=False).exists()
    assert ThreadLabel.objects.filter(thread=thread, place__isnull=True).exists()


@pytest.mark.django_db
def test_deleting_two_labelled_places_on_same_thread_does_not_raise():
    thread = ThreadFactory()
    first = PlaceFactory()
    second = PlaceFactory()
    ThreadLabel.objects.create(thread=thread, place=first)
    ThreadLabel.objects.create(thread=thread, place=second)

    Place.objects.filter(pk__in=[first.pk, second.pk]).delete()

    assert not ThreadLabel.objects.filter(thread=thread).exists()


@pytest.mark.django_db
def test_thread_label_persists_operator_fields():
    user = UserFactory()
    label = ThreadLabel.objects.create(
        thread=ThreadFactory(),
        place=PlaceFactory(),
        is_subject=True,
        is_useful=False,
        created_by=user,
    )

    label.refresh_from_db()

    assert label.is_subject is True
    assert label.is_useful is False
    assert label.created_by == user


def test_thread_label_admin_configuration():
    thread_admin = admin.site._registry[Thread]
    label_admin = admin.site._registry[ThreadLabel]

    assert any(
        isinstance(inline, type) and issubclass(inline, ThreadLabelInline)
        for inline in thread_admin.inlines
    )
    assert HasLabelFilter in thread_admin.list_filter
    assert "created_by" in ThreadLabelInline.readonly_fields
    assert set(label_admin.readonly_fields) == {"created_by", "created_at"}  # type: ignore[attr-defined]


@pytest.mark.django_db
def test_thread_admin_has_label_filter_yes(admin_client):
    labelled = ThreadFactory()
    ThreadLabel.objects.create(thread=labelled, place=PlaceFactory())
    no_place = ThreadFactory()
    ThreadLabel.objects.create(thread=no_place, place=None)
    ThreadFactory()

    response = admin_client.get(
        reverse("admin:watch_thread_changelist"), {"has_label": "yes"}
    )

    assert response.status_code == 200
    assert {thread.pk for thread in response.context["cl"].queryset} == {
        labelled.pk,
        no_place.pk,
    }


@pytest.mark.django_db
def test_thread_admin_has_label_filter_no(admin_client):
    labelled = ThreadFactory()
    ThreadLabel.objects.create(thread=labelled, place=PlaceFactory())
    no_place = ThreadFactory()
    ThreadLabel.objects.create(thread=no_place, place=None)
    unlabelled = ThreadFactory()

    response = admin_client.get(
        reverse("admin:watch_thread_changelist"), {"has_label": "no"}
    )

    assert response.status_code == 200
    assert {thread.pk for thread in response.context["cl"].queryset} == {unlabelled.pk}


@pytest.mark.django_db
def test_thread_label_inline_sets_created_by(admin_client, superuser):
    thread = ThreadFactory()
    place = PlaceFactory()
    data = {
        **_thread_form_data(thread),
        **_management_form(1),
        **_label_form(0, place=str(place.pk), is_subject="on", is_useful="false"),
    }

    response = admin_client.post(
        reverse("admin:watch_thread_change", args=[thread.pk]), data
    )

    assert response.status_code == 302
    label = ThreadLabel.objects.get(thread=thread, place=place)
    assert label.created_by == superuser
    assert label.is_subject is True
    assert label.is_useful is False


@pytest.mark.django_db
def test_thread_label_admin_sets_created_by(admin_client, superuser):
    thread = ThreadFactory()
    place = PlaceFactory()

    response = admin_client.post(
        reverse("admin:watch_threadlabel_add"),
        {
            "thread": str(thread.pk),
            "place": str(place.pk),
            "is_subject": "on",
            "is_useful": "false",
        },
    )

    assert response.status_code == 302
    label = ThreadLabel.objects.get(thread=thread, place=place)
    assert label.created_by == superuser
    assert label.is_subject is True
    assert label.is_useful is False


@pytest.mark.django_db
def test_thread_label_inline_rejects_duplicate_place(admin_client):
    thread = ThreadFactory()
    place = PlaceFactory()
    data = {
        **_thread_form_data(thread),
        **_management_form(2),
        **_label_form(0, place=str(place.pk)),
        **_label_form(1, place=str(place.pk)),
    }

    response = admin_client.post(
        reverse("admin:watch_thread_change", args=[thread.pk]), data
    )

    assert response.status_code == 200
    assert "already has a label for this place" in response.content.decode()
    assert not ThreadLabel.objects.filter(thread=thread).exists()


@pytest.mark.django_db
def test_thread_label_inline_rejects_duplicate_no_place_row(admin_client):
    thread = ThreadFactory()
    data = {
        **_thread_form_data(thread),
        **_management_form(2),
        **_label_form(0, is_useful="false"),
        **_label_form(1, is_useful="false"),
    }

    response = admin_client.post(
        reverse("admin:watch_thread_change", args=[thread.pk]), data
    )

    assert response.status_code == 200
    assert "already has a no-place label" in response.content.decode()
    assert not ThreadLabel.objects.filter(thread=thread).exists()
