import uuid

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import models
from django.template.loader import get_template, render_to_string
from django.utils import timezone
from model_utils.models import TimeStampedModel

from municipalities.models import Muni


class SearchManager(models.Manager):
    def get_or_create_for_params(
        self,
        search_term="",
        municipalities=None,
        states=None,
        date_from=None,
        date_to=None,
        document_type="all",
        meeting_name_query=None,
    ):
        """
        Get or create a Search object for the given parameters.
        Note: This is a simplified version. For exact matching with M2M fields,
        you may need custom logic to find existing searches.
        """
        # Normalize search_term
        search_term = search_term.strip() if search_term else ""
        states = states or []

        # Create the search (M2M relationships set after creation)
        search = self.create(  # type: ignore[assignment]
            search_term=search_term,
            states=states,
            date_from=date_from,
            date_to=date_to,
            document_type=document_type,
            meeting_name_query=meeting_name_query,
        )

        # Set municipalities if provided
        if municipalities:
            search.municipalities.set(municipalities)  # type: ignore[attr-defined]

        return search


class Search(TimeStampedModel):
    """
    Represents a search configuration that queries local MeetingPage database.
    Supports full filter capabilities including multiple municipalities, states,
    date ranges, document types, and meeting name queries.
    """

    DOCUMENT_TYPE_CHOICES = [
        ("all", "All"),
        ("agenda", "Agenda"),
        ("minutes", "Minutes"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Filter fields
    municipalities = models.ManyToManyField(Muni, related_name="searches", blank=True)
    search_term = models.CharField(
        max_length=500,
        blank=True,
        null=True,
        help_text="Search query. Empty/null for 'all updates' mode.",
    )
    states = models.JSONField(
        default=list, blank=True, help_text="List of state codes to filter by"
    )
    date_from = models.DateField(null=True, blank=True)
    date_to = models.DateField(null=True, blank=True)
    document_type = models.CharField(
        max_length=10, choices=DOCUMENT_TYPE_CHOICES, default="all"
    )
    meeting_name_query = models.CharField(
        max_length=500,
        blank=True,
        null=True,
        help_text="Full-text search on meeting names",
    )

    # Result tracking
    last_result_page_ids = models.JSONField(
        default=list,
        blank=True,
        help_text="List of page IDs from last search execution (for change detection)",
    )
    last_result_count = models.IntegerField(
        default=0, help_text="Number of pages matched in last search"
    )
    last_fetched = models.DateTimeField(null=True, blank=True)

    objects = SearchManager()

    class Meta:
        verbose_name = "Search"
        verbose_name_plural = "Searches"
        ordering = ["-created"]

    def __str__(self) -> str:
        if self.search_term:
            munis = self.municipalities.all()[:2]
            muni_names = ", ".join(m.name for m in munis)
            if self.municipalities.count() > 2:
                muni_names += f", +{self.municipalities.count() - 2} more"
            return f"Search for '{self.search_term}' in {muni_names or 'all municipalities'}"
        return f"All updates search ({self.municipalities.count()} municipalities)"

    def update_search(self):
        """
        Execute search against local MeetingPage database and return new pages.
        Updates last_result_page_ids and last_result_count with current results.

        Returns:
            QuerySet of MeetingPage objects that are new since last check.
        """
        from .services import execute_search, get_new_pages

        # Get only new pages (not in last_result_page_ids)
        new_pages = get_new_pages(self)

        # Update tracking fields with ALL current results
        all_current_results = execute_search(self)
        self.last_result_page_ids = list(
            all_current_results.values_list("id", flat=True)
        )
        self.last_result_count = all_current_results.count()
        self.last_fetched = timezone.now()
        self.save()

        return new_pages


class SavedSearch(TimeStampedModel):
    """
    Links a user to a Search configuration with notification preferences.
    Supports configurable notification frequencies: immediate, daily, or weekly.
    """

    NOTIFICATION_FREQUENCY_CHOICES = [
        ("immediate", "Immediate"),
        ("daily", "Daily Digest"),
        ("weekly", "Weekly Digest"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="saved_searches",
    )
    search = models.ForeignKey(
        Search, on_delete=models.CASCADE, related_name="saved_by"
    )
    name = models.CharField(max_length=200, help_text="A name for this saved search")

    # Notification settings
    notification_frequency = models.CharField(
        max_length=10,
        choices=NOTIFICATION_FREQUENCY_CHOICES,
        default="immediate",
        help_text="How often to send notifications for new results",
    )
    last_notification_sent = models.DateTimeField(
        null=True, blank=True, help_text="When the last notification email was sent"
    )
    last_checked = models.DateTimeField(
        default=timezone.now, help_text="When this saved search was last checked"
    )
    has_pending_results = models.BooleanField(
        default=False,
        help_text="True if there are new results waiting to be sent in digest",
    )

    class Meta:
        verbose_name = "Saved Search"
        verbose_name_plural = "Saved Searches"
        ordering = ["-created"]
        unique_together = ["user", "search"]

    def __str__(self) -> str:
        return f"{self.name} - {self.user.email}"

    def send_search_notification(self, new_pages=None) -> None:
        """
        Send notification email with new search results.

        Args:
            new_pages: QuerySet of MeetingPage objects (new matches).
                      If None, uses legacy template format.
        """
        context = {"subscription": self, "new_pages": new_pages}
        txt_content = render_to_string("email/search_update.txt", context=context)
        html_content = get_template("email/search_update.html").render(context=context)
        msg = EmailMultiAlternatives(
            subject=f"New Results for {self.name}",
            to=[self.user.email],
            from_email="Civic Observer <noreply@civic.observer>",
            body=txt_content,
        )
        msg.attach_alternative(html_content, "text/html")
        msg.esp_extra = {"MessageStream": "outbound"}  # type: ignore

        msg.send()

        # Update the last notification sent timestamp and clear pending flag
        self.last_notification_sent = timezone.now()
        self.has_pending_results = False
        self.save(update_fields=["last_notification_sent", "has_pending_results"])
