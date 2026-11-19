import pytest
from django.utils import timezone

from tests.factories import (
    IngestRunFactory,
    MeetingHitFactory,
    MentionFactory,
    PlaceFactory,
    PlaceWithMuniFactory,
    SubredditFactory,
    ThreadFactory,
)
from watch.models import Mention, MutedCountry, Place, Thread


@pytest.mark.django_db
def test_mention_id_is_primary_key():
    mention = MentionFactory(id="F5BOT-alert-1-1")
    assert Mention.objects.get(pk="F5BOT-alert-1-1") == mention


@pytest.mark.django_db
def test_place_geoid_unique_per_type_not_globally():
    city = PlaceFactory(geoid="1234567", canonical_key="us:place:1234567")
    school = PlaceFactory(
        geoid="1234567", canonical_key="us:school:1234567", place_type="school_district"
    )
    assert Place.objects.filter(geoid="1234567").count() == 2
    assert city.place_type != school.place_type


@pytest.mark.django_db
def test_places_can_share_null_geoid():
    PlaceFactory(geoid=None)
    PlaceFactory(geoid=None)
    assert Place.objects.filter(geoid__isnull=True).count() == 2


@pytest.mark.django_db
def test_factories_create_linked_defaults():
    place = PlaceWithMuniFactory()
    subreddit = SubredditFactory(place=place)
    thread = ThreadFactory(subreddit=subreddit)
    mention = MentionFactory(thread=thread)
    run = IngestRunFactory()

    assert place.muni is not None
    assert place.muni_link_status == "auto"
    assert subreddit.kind == "general"
    assert thread.status == "new"
    assert thread.mentions.get() == mention
    assert mention.highlights == []
    assert mention.raw_json == {}
    assert run.finished_at is not None
    assert (run.pages, run.seen, run.new, run.skipped) == (1, 0, 0, 0)


@pytest.mark.django_db
def test_json_defaults_are_independent():
    first = MentionFactory()
    second = MentionFactory()
    first.highlights.append("highlight")
    first.raw_json["id"] = "first"
    assert second.highlights == []
    assert second.raw_json == {}


@pytest.mark.django_db
def test_ordering_and_muted_country_created_timestamp():
    older = ThreadFactory(last_seen=timezone.now() - timezone.timedelta(days=1))
    newer = ThreadFactory(last_seen=timezone.now())
    assert list(Thread.objects.all()) == [newer, older]
    muted = MutedCountry.objects.create(pk="US")
    assert muted.created is not None


@pytest.mark.django_db
def test_meeting_hit_factory_does_not_create_page():
    hit = MeetingHitFactory()
    assert hit.page_id == "page-1"
    assert hit.query_hash == "0" * 64
