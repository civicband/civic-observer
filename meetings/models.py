import uuid

from django.db import models
from model_utils.models import TimeStampedModel


class MeetingDocument(TimeStampedModel):
    """
    Represents a meeting document (agenda or minutes) for a municipality.
    Groups individual pages together.
    """

    DOCUMENT_TYPE_CHOICES = [
        ("agenda", "Agenda"),
        ("minutes", "Minutes"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    municipality = models.ForeignKey(
        "municipalities.Muni",
        on_delete=models.CASCADE,
        related_name="meetings",
        db_index=True,
    )
    meeting_name = models.CharField(
        max_length=255,
        db_index=True,
        help_text="Name of the meeting body (e.g., 'CityCouncil', 'PlanningBoard')",
    )
    meeting_date = models.DateField(db_index=True)
    document_type = models.CharField(
        max_length=10, choices=DOCUMENT_TYPE_CHOICES, db_index=True
    )

    class Meta:
        verbose_name = "Meeting Document"
        verbose_name_plural = "Meeting Documents"
        ordering = ["-meeting_date", "meeting_name"]
        indexes = [
            models.Index(
                fields=["municipality", "meeting_name", "meeting_date"],
                name="meetings_muni_name_date_idx",
            ),
            models.Index(
                fields=["municipality", "document_type"],
                name="meetings_muni_type_idx",
            ),
            # Optimized composite index for common search filter patterns
            models.Index(
                fields=["municipality", "document_type", "meeting_date"],
                name="meetings_muni_type_date_idx",
            ),
        ]
        unique_together = [
            ["municipality", "meeting_name", "meeting_date", "document_type"]
        ]

    def __str__(self) -> str:
        return f"{self.municipality.subdomain} - {self.meeting_name} {self.document_type} ({self.meeting_date})"

    def civic_band_table_name(self) -> str:
        if self.document_type == "agenda":
            return "agendas"
        return "minutes"


class MeetingPage(TimeStampedModel):
    """
    Represents an individual page of a meeting document.
    Contains the text content and reference to the page image.
    """

    # Use the civic.band ID as primary key to avoid duplicates
    id = models.CharField(max_length=255, primary_key=True)
    document = models.ForeignKey(
        MeetingDocument, on_delete=models.CASCADE, related_name="pages"
    )
    page_number = models.IntegerField()
    text = models.TextField(blank=True)
    page_image = models.CharField(
        max_length=500,
        blank=True,
        help_text="Path to the page image (e.g., '/_agendas/CityCouncil/2024-01-02/5.png')",
    )

    # --- from clerk ---------------------------------------------------------
    legacy_id = models.CharField(max_length=64, null=True, blank=True, db_index=True)
    content_hash = models.CharField(max_length=64, null=True, blank=True)

    # --- denormalized for BM25 ---------------------------------------------
    # db_constraint=False: this duplicates document.municipality, so the FK
    # check on every COPY row costs throughput for integrity already enforced
    # upstream. db_index=False: the BM25 index covers these; a separate btree
    # is dead weight on 15.5M rows.
    municipality = models.ForeignKey(
        "municipalities.Muni",
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        db_index=False,
        null=True,
        related_name="+",
    )
    municipality_subdomain = models.CharField(max_length=255, blank=True)
    municipality_name = models.CharField(max_length=255, blank=True)
    state = models.CharField(max_length=64, blank=True)
    meeting_name = models.CharField(max_length=255, blank=True)
    meeting_date = models.DateField(null=True)
    document_type = models.CharField(max_length=32, blank=True)

    class Meta:
        verbose_name = "Meeting Page"
        verbose_name_plural = "Meeting Pages"
        ordering = ["document", "page_number"]
        unique_together = [["document", "page_number"]]
        # NOTE: no indexes here. The BM25 index is created out-of-band in
        # Stage 6 — never in a migration. See migration 0008's docstring for
        # what happens when a large index build runs inside a deploy.

    @property
    def civic_band_table_name(self) -> str:
        return "agendas" if self.document_type == "agenda" else "minutes"

    def denormalize(self, document=None):
        """Populate the flattened columns from the parent document."""
        doc = document or self.document
        muni = doc.municipality
        self.municipality_id = muni.id
        self.municipality_subdomain = muni.subdomain
        self.municipality_name = muni.name
        self.state = muni.state
        self.meeting_name = doc.meeting_name
        self.meeting_date = doc.meeting_date
        self.document_type = doc.document_type

    def save(self, *args, **kwargs):
        # Always refresh: a page can be reassigned to a different document, and
        # a stale denormalized row is worse than a slightly slower save. The
        # three existing call sites all have `document` in hand already, so
        # this is usually free.
        if self.document_id:
            self.denormalize()
        super().save(*args, **kwargs)


class BackfillProgress(models.Model):
    """
    Tracks progress of backfill operations for municipalities.

    Stores checkpoints for resumable backfills and configuration flags
    for controlling backfill mode (full vs incremental).
    """

    DOCUMENT_TYPE_CHOICES = [
        ("agenda", "Agenda"),
        ("minutes", "Minutes"),
    ]

    MODE_CHOICES = [
        ("full", "Full"),
        ("incremental", "Incremental"),
    ]

    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("in_progress", "In Progress"),
        ("completed", "Completed"),
        ("failed", "Failed"),
    ]

    municipality = models.ForeignKey(
        "municipalities.Muni",
        on_delete=models.CASCADE,
        related_name="backfill_progress",
    )
    document_type = models.CharField(
        max_length=20,
        choices=DOCUMENT_TYPE_CHOICES,
    )
    mode = models.CharField(
        max_length=20,
        choices=MODE_CHOICES,
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="pending",
    )
    next_cursor = models.TextField(
        blank=True,
        null=True,
        help_text="Pagination cursor for resuming backfill",
    )
    force_full_backfill = models.BooleanField(
        default=False,
        help_text="Set to True to force full backfill on next run",
    )
    started_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    error_message = models.TextField(
        blank=True,
        null=True,
        help_text="Error message if backfill failed",
    )

    class Meta:
        unique_together = [["municipality", "document_type"]]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["municipality", "status"]),
        ]
        verbose_name = "Backfill Progress"
        verbose_name_plural = "Backfill Progress"

    def __str__(self) -> str:
        return f"{self.municipality.subdomain} - {self.document_type} ({self.status})"


class BackfillJob(TimeStampedModel):
    """
    Tracks progress and state for municipality meeting data backfill operations.

    Provides checkpoint/resume capability so large backfills can recover from
    failures without starting over.
    """

    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("running", "Running"),
        ("completed", "Completed"),
        ("failed", "Failed"),
        ("paused", "Paused"),
    ]

    municipality = models.ForeignKey(
        "municipalities.Muni",
        on_delete=models.CASCADE,
        related_name="backfill_jobs",
    )
    document_type = models.CharField(
        max_length=10,
        choices=MeetingDocument.DOCUMENT_TYPE_CHOICES,
        help_text="Type of document being backfilled (agenda or minutes)",
    )

    # State tracking
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="pending",
        db_index=True,
    )

    # Progress tracking (checkpoint data)
    last_cursor = models.TextField(
        blank=True,
        default="",
        help_text="Pagination cursor to resume from if interrupted",
    )
    pages_fetched = models.IntegerField(
        default=0,
        help_text="Total number of page records fetched from API",
    )
    pages_created = models.IntegerField(
        default=0,
        help_text="Number of new MeetingPage records created",
    )
    pages_updated = models.IntegerField(
        default=0,
        help_text="Number of existing MeetingPage records updated",
    )
    errors_encountered = models.IntegerField(
        default=0,
        help_text="Number of errors encountered during backfill",
    )

    # Verification data
    expected_count = models.IntegerField(
        null=True,
        blank=True,
        help_text="Expected total page count from API metadata",
    )
    actual_count = models.IntegerField(
        null=True,
        blank=True,
        help_text="Actual page count in local database after backfill",
    )
    verified_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp when verification was performed",
    )

    # Error details
    last_error = models.TextField(
        blank=True,
        default="",
        help_text="Last error message encountered",
    )
    retry_count = models.IntegerField(
        default=0,
        help_text="Number of retry attempts for this job",
    )

    class Meta:
        verbose_name = "Backfill Job"
        verbose_name_plural = "Backfill Jobs"
        ordering = ["-created"]
        indexes = [
            models.Index(
                fields=["municipality", "document_type", "status"],
                name="backfill_muni_type_status_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.municipality.subdomain} - {self.document_type} - {self.status}"
