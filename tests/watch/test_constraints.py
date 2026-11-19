import pytest
from django.db import IntegrityError, transaction

from meetings.models import MeetingPage
from tests.factories import (
    MeetingHitFactory,
    MentionFactory,
    PlaceFactory,
    ThreadFactory,
    ThreadPlaceFactory,
)
from watch.models import MeetingHit, PlaceAlias, PlaceMention


@pytest.mark.django_db
def test_thread_key_is_unique():
    ThreadFactory(key="reddit:test/1")
    with pytest.raises(IntegrityError), transaction.atomic():
        ThreadFactory(key="reddit:test/1")


@pytest.mark.django_db
def test_duplicate_geoid_and_place_type_are_rejected():
    PlaceFactory(geoid="1234567", place_type="city")
    with pytest.raises(IntegrityError), transaction.atomic():
        PlaceFactory(geoid="1234567", place_type="city")


@pytest.mark.django_db
def test_thread_place_is_unique_per_thread_and_place():
    link = ThreadPlaceFactory()
    with pytest.raises(IntegrityError), transaction.atomic():
        ThreadPlaceFactory(thread=link.thread, place=link.place, role="mentioned")


@pytest.mark.django_db
def test_meeting_hit_accepts_missing_page():
    hit = MeetingHitFactory(page_id="missing-page")
    assert not MeetingPage.objects.filter(pk="missing-page").exists()
    assert MeetingHit.objects.get(pk=hit.pk).page_id == "missing-page"


@pytest.mark.django_db
def test_meeting_hit_is_unique_per_thread_place_page_and_query():
    hit = MeetingHitFactory()
    with pytest.raises(IntegrityError), transaction.atomic():
        MeetingHitFactory(
            thread=hit.thread,
            place=hit.place,
            page_id=hit.page_id,
            query_hash=hit.query_hash,
        )
    MeetingHitFactory(thread=hit.thread, place=hit.place, query_hash="1" * 64)


@pytest.mark.django_db
def test_place_alias_is_unique_per_place_key_and_kind():
    place = PlaceFactory()
    PlaceAlias.objects.create(
        place=place, key="testville", kind="name", source="manual"
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        PlaceAlias.objects.create(
            place=place, key="testville", kind="name", source="gazetteer"
        )
    PlaceAlias.objects.create(
        place=place, key="testville", kind="variant", source="manual"
    )


@pytest.mark.django_db
def test_place_mention_is_unique_per_mention_and_place():
    mention = MentionFactory()
    place = PlaceFactory()
    PlaceMention.objects.create(
        mention=mention,
        place=place,
        matched_text="Testville",
        method="manual",
        confidence="high",
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        PlaceMention.objects.create(
            mention=mention,
            place=place,
            matched_text="Testville",
            method="manual",
            confidence="low",
        )
