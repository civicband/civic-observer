import uuid
from datetime import datetime

from django.conf import settings
from django.db import models
from django.utils import timezone
from django_countries.fields import CountryField
from model_utils.models import TimeStampedModel


class Place(TimeStampedModel):
    class PlaceType(models.TextChoices):
        STATE = "state", "State"
        COUNTY = "county", "County"
        COUNTY_SUBDIVISION = "county_subdivision", "County subdivision"
        CITY = "city", "City"
        TOWN = "town", "Town"
        VILLAGE = "village", "Village"
        CDP = "cdp", "CDP"
        SCHOOL_DISTRICT = "school_district", "School district"
        CANADIAN_CITY = "canadian_city", "Canadian city"
        COUNTRY = "country", "Country"

    class MuniLinkStatus(models.TextChoices):
        UNLINKED = "unlinked", "Unlinked"
        AUTO = "auto", "Auto"
        CONFIRMED = "confirmed", "Confirmed"
        NO_MATCH = "no_match", "No match"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    canonical_key = models.CharField(max_length=255, unique=True)
    geoid = models.CharField(max_length=20, null=True, blank=True)
    name = models.CharField(max_length=255, db_index=True)
    legal_name = models.CharField(max_length=255, null=True, blank=True)
    place_type = models.CharField(max_length=32, choices=PlaceType)
    state = models.CharField(max_length=10, blank=True, db_index=True)
    country = CountryField(default="US")
    county = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="places_in_county",
    )
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    population = models.PositiveIntegerField(null=True, blank=True, db_index=True)
    is_government = models.BooleanField(default=False, db_index=True)
    muni = models.ForeignKey(
        "municipalities.Muni",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="watch_places",
    )
    muni_link_status = models.CharField(
        max_length=16, choices=MuniLinkStatus, default=MuniLinkStatus.UNLINKED
    )
    muni_reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["geoid", "place_type"],
                condition=models.Q(geoid__isnull=False),
                name="watch_place_unique_geoid_type",
            )
        ]
        indexes = [
            models.Index(
                fields=["place_type", "state"], name="watch_place_type_state_idx"
            ),
            models.Index(
                fields=["is_government", "state"], name="watch_place_gov_state_idx"
            ),
        ]

    def __str__(self):
        """Return a concise admin label."""
        return f"{self.name} ({self.state})" if self.state else self.name


class Subreddit(TimeStampedModel):
    class Kind(models.TextChoices):
        PLACE = "place", "Place"
        REGION = "region", "Region"
        STATE = "state", "State"
        NON_US = "non_us", "Non-US"
        GENERAL = "general", "General"
        AMBIGUOUS = "ambiguous", "Ambiguous"

    class Confidence(models.TextChoices):
        HIGH = "high", "High"
        MEDIUM = "medium", "Medium"
        LOW = "low", "Low"

    class Source(models.TextChoices):
        MANUAL = "manual", "Manual"
        WIKIDATA = "wikidata", "Wikidata"
        GAZETTEER = "gazetteer", "Gazetteer"
        TEXT_EVIDENCE = "text_evidence", "Text evidence"
        LLM = "llm", "LLM"

    name = models.CharField(max_length=255, primary_key=True)
    kind = models.CharField(max_length=16, choices=Kind, db_index=True)
    place = models.ForeignKey(Place, on_delete=models.SET_NULL, null=True, blank=True)
    state = models.CharField(max_length=10, blank=True)
    country = CountryField(null=True, blank=True)
    confidence = models.CharField(max_length=8, choices=Confidence, blank=True)
    source = models.CharField(max_length=16, choices=Source, blank=True, db_index=True)
    muted = models.BooleanField(default=False, db_index=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    classified_at = models.DateTimeField(null=True, blank=True, db_index=True)
    note = models.TextField(blank=True)

    def __str__(self):
        """Return a concise admin label."""
        return f"r/{self.name}"


class Thread(TimeStampedModel):
    class Status(models.TextChoices):
        NEW = "new", "New"
        REPLIED = "replied", "Replied"
        DISMISSED = "dismissed", "Dismissed"
        SNOOZED = "snoozed", "Snoozed"
        BACKLOG = "backlog", "Backlog"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    key = models.CharField(max_length=255, unique=True)
    subreddit = models.ForeignKey(
        Subreddit,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="threads",
    )
    title = models.TextField(blank=True)
    first_seen = models.DateTimeField()
    last_seen = models.DateTimeField()
    mention_count = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=16, choices=Status, default=Status.NEW)
    snoozed_until = models.DateTimeField(null=True, blank=True)
    dismissed_at = models.DateTimeField(null=True, blank=True, editable=False)
    notes = models.TextField(blank=True)
    duplicate_of = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="duplicates",
    )

    class Meta:
        ordering = ["-last_seen"]
        indexes = [
            models.Index(
                fields=["status", "last_seen"], name="watch_thread_status_seen_idx"
            ),
            models.Index(
                fields=["subreddit", "last_seen"], name="watch_thread_sub_seen_idx"
            ),
        ]

    def __str__(self):
        """Return a concise admin label."""
        return self.title or self.key

    def dismiss(self, *, now: datetime | None = None) -> None:
        """Mark this thread dismissed and record when it was closed."""
        timestamp = now or timezone.now()
        self.status = Thread.Status.DISMISSED
        self.snoozed_until = None
        self.dismissed_at = timestamp
        self.save(
            update_fields=[
                "status",
                "snoozed_until",
                "dismissed_at",
                "modified",
            ]
        )


def dismiss_threads(
    queryset: models.QuerySet[Thread], *, now: datetime | None = None
) -> int:
    """Mark a queryset of threads dismissed and record when they were closed."""
    timestamp = now or timezone.now()
    return queryset.update(
        status=Thread.Status.DISMISSED,
        snoozed_until=None,
        dismissed_at=timestamp,
        modified=timestamp,
    )


class ThreadLabel(models.Model):
    """Operator-supplied judgement about a thread's places.

    A row with ``place=None`` is still a label: it records that the operator
    reviewed the thread and concluded it has no associated place.  A label for
    a deleted place is removed rather than nulled: nulling it would collide
    with the one-no-place-per-thread constraint.
    """

    thread = models.ForeignKey(Thread, on_delete=models.CASCADE, related_name="labels")
    place = models.ForeignKey(
        Place,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="labels",
    )
    is_subject = models.BooleanField(default=False)
    is_useful = models.BooleanField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["thread", "place"],
                condition=models.Q(place__isnull=False),
                name="watch_threadlabel_place_uniq",
            ),
            models.UniqueConstraint(
                fields=["thread"],
                condition=models.Q(place__isnull=True),
                name="watch_threadlabel_noplace_uniq",
            ),
        ]

    def __str__(self):
        """Return a concise admin label."""
        return f"{self.thread} - {self.place or 'no place'}"


class Mention(models.Model):
    class Kind(models.TextChoices):
        POST = "post", "Post"
        COMMENT = "comment", "Comment"
        OTHER = "other", "Other"

    id = models.CharField(max_length=255, primary_key=True)
    thread = models.ForeignKey(
        Thread, on_delete=models.CASCADE, related_name="mentions"
    )
    alert_num = models.BigIntegerField(null=True, blank=True)
    url = models.URLField(max_length=1000)
    site = models.CharField(max_length=32, default="reddit")
    kind = models.CharField(max_length=16, choices=Kind)
    source_tag = models.CharField(max_length=255, blank=True)
    keyword = models.CharField(max_length=255, blank=True, db_index=True)
    subreddit_name = models.CharField(max_length=255, blank=True)
    post_id = models.CharField(max_length=64, blank=True)
    comment_id = models.CharField(max_length=64, blank=True)
    post_title = models.TextField(blank=True)
    snippet_text = models.TextField(blank=True)
    snippet_html = models.TextField(blank=True)
    highlights = models.JSONField(default=list)
    author = models.CharField(max_length=255, blank=True)
    grp = models.CharField(max_length=255, blank=True)
    published_at = models.DateTimeField(null=True, blank=True, db_index=True)
    raw_json = models.JSONField(default=dict)
    ingested_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-published_at"]
        indexes = [
            models.Index(
                fields=["thread", "published_at"], name="watch_mention_thread_pub_idx"
            ),
            models.Index(
                fields=["keyword", "published_at"], name="watch_mention_key_pub_idx"
            ),
        ]

    def __str__(self):
        """Return a concise admin label."""
        return self.post_title or self.id


class PlaceAlias(TimeStampedModel):
    class Kind(models.TextChoices):
        NAME = "name", "Name"
        NAME_STATE = "name_state", "Name and state"
        NAME_STATE_NAME = "name_state_name", "Name and state name"
        NAME_CITY = "name_city", "Name and city"
        NICKNAME = "nickname", "Nickname"
        VARIANT = "variant", "Variant"
        SUBREDDIT = "subreddit", "Subreddit"

    class Source(models.TextChoices):
        GAZETTEER = "gazetteer", "Gazetteer"
        GEONAMES = "geonames", "GeoNames"
        WIKIDATA = "wikidata", "Wikidata"
        MANUAL = "manual", "Manual"

    place = models.ForeignKey(Place, on_delete=models.CASCADE, related_name="aliases")
    key = models.CharField(max_length=255, db_index=True)
    kind = models.CharField(max_length=16, choices=Kind)
    source = models.CharField(max_length=16, choices=Source)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["place", "key", "kind"], name="watch_alias_unique"
            )
        ]


class NonUsCity(models.Model):
    geoname_id = models.BigIntegerField(unique=True)
    name = models.CharField(max_length=255)
    normalized_name = models.CharField(max_length=255, db_index=True)
    country = CountryField()
    admin1 = models.CharField(max_length=10, blank=True)
    population = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["name"]
        verbose_name = "non-US city"
        verbose_name_plural = "non-US cities"

    def __str__(self):
        """Return a concise admin label."""
        return f"{self.name} ({self.country})"


class NonUsCityAlias(models.Model):
    city = models.ForeignKey(
        NonUsCity, on_delete=models.CASCADE, related_name="aliases"
    )
    key = models.CharField(max_length=255, db_index=True)

    class Meta:
        ordering = ["key"]
        constraints = [
            models.UniqueConstraint(
                fields=["city", "key"], name="watch_nonuscityalias_unique"
            )
        ]

    def __str__(self):
        """Return a concise admin label."""
        return self.key


class PlaceMention(TimeStampedModel):
    class Method(models.TextChoices):
        BODY_PATTERN = "body_pattern", "Body pattern"
        GAZETTEER_PHRASE = "gazetteer_phrase", "Gazetteer phrase"
        LLM = "llm", "LLM"
        MANUAL = "manual", "Manual"

    class Confidence(models.TextChoices):
        HIGH = "high", "High"
        MEDIUM = "medium", "Medium"
        LOW = "low", "Low"

    mention = models.ForeignKey(
        Mention, on_delete=models.CASCADE, related_name="place_mentions"
    )
    place = models.ForeignKey(Place, on_delete=models.CASCADE, related_name="mentions")
    matched_text = models.CharField(max_length=255)
    method = models.CharField(max_length=16, choices=Method)
    confidence = models.CharField(max_length=8, choices=Confidence)
    is_subject = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["mention", "place"], name="watch_placemention_unique"
            )
        ]


class ThreadPlace(TimeStampedModel):
    class Role(models.TextChoices):
        SUBJECT = "subject", "Subject"
        MENTIONED = "mentioned", "Mentioned"

    class Confidence(models.TextChoices):
        HIGH = "high", "High"
        MEDIUM = "medium", "Medium"
        LOW = "low", "Low"

    class Source(models.TextChoices):
        SUBREDDIT = "subreddit", "Subreddit"
        POST = "post", "Post"
        COMMENT = "comment", "Comment"
        MANUAL = "manual", "Manual"

    thread = models.ForeignKey(
        Thread, on_delete=models.CASCADE, related_name="thread_places"
    )
    place = models.ForeignKey(
        Place, on_delete=models.CASCADE, related_name="thread_places"
    )
    role = models.CharField(max_length=16, choices=Role)
    confidence = models.CharField(max_length=8, choices=Confidence)
    source = models.CharField(max_length=16, choices=Source)
    query_override = models.CharField(max_length=500, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["thread", "place"], name="watch_threadplace_unique"
            )
        ]
        indexes = [
            models.Index(fields=["place", "role"], name="watch_tp_place_role_idx"),
            models.Index(fields=["thread", "role"], name="watch_tp_thread_role_idx"),
        ]


class MeetingHit(TimeStampedModel):
    thread = models.ForeignKey(
        Thread, on_delete=models.CASCADE, related_name="meeting_hits"
    )
    place = models.ForeignKey(
        Place, on_delete=models.CASCADE, related_name="meeting_hits"
    )
    page = models.ForeignKey(
        "meetings.MeetingPage",
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="+",
    )
    query = models.TextField(blank=True)
    query_hash = models.CharField(max_length=64)
    # Optional: the current civic-observer backend does not return a rank, so
    # cached hits normally store ``None``.  Kept for backends that supply one.
    rank = models.FloatField(null=True, blank=True)
    snippet = models.TextField(blank=True)
    meeting_name = models.CharField(max_length=255, blank=True)
    meeting_date = models.DateField(null=True, blank=True)
    document_type = models.CharField(max_length=32, blank=True)
    civic_band_url = models.URLField(max_length=1000, blank=True)
    thumbnail_url = models.URLField(max_length=1000, blank=True)

    class Meta:
        ordering = ["meeting_date", "meeting_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["thread", "place", "page", "query_hash"],
                name="watch_meetinghit_unique",
            )
        ]
        indexes = [
            models.Index(fields=["thread", "place"], name="watch_hit_thread_place_idx"),
            models.Index(
                fields=["place", "meeting_date"], name="watch_hit_place_date_idx"
            ),
        ]

    @property
    def is_future(self) -> bool:
        """Return whether the meeting date is after today."""
        return bool(self.meeting_date and self.meeting_date > timezone.localdate())


class IngestRun(TimeStampedModel):
    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)
    source = models.CharField(max_length=255, blank=True)
    pages = models.PositiveIntegerField(default=0)
    seen = models.PositiveIntegerField(default=0)
    new = models.PositiveIntegerField(default=0)
    skipped = models.PositiveIntegerField(default=0)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ["-started_at"]


class PlaceNote(TimeStampedModel):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        MAYBE = "maybe", "Maybe"
        REJECTED = "rejected", "Rejected"
        ADDED = "added", "Added"

    place = models.ForeignKey(Place, on_delete=models.CASCADE, related_name="notes")
    text = models.TextField()
    status = models.CharField(max_length=16, choices=Status, default=Status.OPEN)


class MutedCountry(models.Model):
    code = models.CharField(max_length=2, primary_key=True)
    created = models.DateTimeField(auto_now_add=True)
