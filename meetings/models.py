import uuid

from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVectorField
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
    # Pre-computed search vector for fast full-text search
    # Auto-updated by database trigger (see migration 0003)
    search_vector = SearchVectorField(null=True)

    class Meta:
        verbose_name = "Meeting Page"
        verbose_name_plural = "Meeting Pages"
        ordering = ["document", "page_number"]
        indexes = [
            models.Index(
                fields=["document", "page_number"], name="meetings_doc_page_idx"
            ),
            GinIndex(
                fields=["text"],
                name="meetingpage_text_gin_idx",
                opclasses=["gin_trgm_ops"],
            ),
        ]
        unique_together = [["document", "page_number"]]

    def __str__(self) -> str:
        return f"{self.document} - Page {self.page_number}"
