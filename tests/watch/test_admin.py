import pytest
from django.contrib import admin
from django.urls import reverse

from tests.factories import IngestRunFactory, MentionFactory, ThreadPlaceFactory
from watch.models import IngestRun, Mention, ThreadPlace


@pytest.mark.django_db
def test_admin_lists_threads_and_ingest_runs(admin_client):
    MentionFactory()
    IngestRunFactory()
    assert admin_client.get(reverse("admin:watch_thread_changelist")).status_code == 200
    assert (
        admin_client.get(reverse("admin:watch_ingestrun_changelist")).status_code == 200
    )
    assert (
        admin_client.get(reverse("admin:watch_mention_changelist")).status_code == 200
    )


def test_ingest_run_admin_is_read_only_and_newest_first():
    model_admin = admin.site._registry[IngestRun]
    assert model_admin.ordering == ["-started_at"]
    all_fields = {field.name for field in IngestRun._meta.fields}
    assert all_fields <= set(model_admin.readonly_fields)  # type: ignore[attr-defined]
    assert model_admin.has_add_permission(None) is False  # type: ignore[arg-type]
    assert model_admin.has_change_permission(None) is False  # type: ignore[arg-type]
    assert model_admin.has_delete_permission(None) is False  # type: ignore[arg-type]


@pytest.mark.django_db
def test_thread_place_admin_edit_marks_source_manual(admin_client):
    thread_place = ThreadPlaceFactory(source="post", role="subject")

    response = admin_client.post(
        reverse("admin:watch_threadplace_change", args=[thread_place.pk]),
        {
            "thread": str(thread_place.thread_id),
            "place": str(thread_place.place_id),
            "role": "mentioned",
            "confidence": "low",
            "query_override": "",
        },
    )

    assert response.status_code == 302
    thread_place.refresh_from_db()
    assert (thread_place.role, thread_place.source) == ("mentioned", "manual")


def test_thread_place_admin_source_is_read_only():
    model_admin = admin.site._registry[ThreadPlace]
    assert "source" in model_admin.readonly_fields  # type: ignore[attr-defined]


def test_mention_admin_is_read_only_and_hides_raw_json():
    model_admin = admin.site._registry[Mention]
    assert "raw_json" not in model_admin.list_display
    all_fields = {field.name for field in Mention._meta.fields}
    assert all_fields <= set(model_admin.readonly_fields)  # type: ignore[attr-defined]
    assert model_admin.has_add_permission(None) is False  # type: ignore[arg-type]
    assert model_admin.has_change_permission(None) is False  # type: ignore[arg-type]
    assert model_admin.has_delete_permission(None) is False  # type: ignore[arg-type]
