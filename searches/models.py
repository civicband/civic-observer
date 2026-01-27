import uuid

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import models
from django.template.loader import get_template, render_to_string
from django.urls import reverse
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

        Searches for existing Search with matching parameters including M2M municipalities.
        Creates new Search only if no exact match is found.

        Returns:
            Search object (either existing or newly created)
        """
        # Normalize inputs
        search_term = search_term.strip() if search_term else ""
        meeting_name_query = meeting_name_query.strip() if meeting_name_query else ""
        states = states or []
        municipalities = municipalities or []

        # Convert municipalities to list of IDs for comparison
        if municipalities:
            if hasattr(municipalities[0], "pk"):
                # List of model instances
                muni_ids = sorted([m.pk for m in municipalities])
            else:
                # Already a list of IDs
                muni_ids = sorted(municipalities)
        else:
            muni_ids = []

        # Find candidates with matching scalar fields
        candidates = self.filter(
            search_term=search_term,
            states=states,
            date_from=date_from,
            date_to=date_to,
            document_type=document_type,
            meeting_name_query=meeting_name_query,
        ).prefetch_related("municipalities")

        # Check each candidate for matching municipalities
        for candidate in candidates:
            candidate_muni_ids = sorted(
                candidate.municipalities.values_list("pk", flat=True)  # type: ignore[attr-defined]
            )
            if candidate_muni_ids == muni_ids:
                # Found exact match
                return candidate

        # No match found - create new Search
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
        default="",
        help_text="Search query. Empty string for 'all updates' mode.",
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
        default="",
        help_text="Full-text search on meeting names",
    )

    # Result tracking
    last_checked_for_new_pages = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp of last check for new pages (for change detection)",
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

    @property
    def muni(self) -> Muni | None:
        """Return the first municipality for backwards compatibility with templates."""
        return self.municipalities.first()

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
        Updates last_checked_for_new_pages timestamp and last_result_count.

        Returns:
            QuerySet of MeetingPage objects that are new since last check.
        """
        from .services import execute_search, get_new_pages

        # Get only new pages (created since last check)
        new_pages = get_new_pages(self)

        # Update tracking fields with current timestamp and count
        all_current_results = execute_search(self)
        self.last_result_count = all_current_results.count()
        self.last_checked_for_new_pages = timezone.now()
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
    notification_channels = models.JSONField(
        default=dict,
        blank=True,
        help_text='Channel overrides: {"channels": ["discord", "email"]}',
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

    def get_effective_channels(self):
        """
        Get the notification channels to use for this saved search.

        If notification_channels has a "channels" key, use only those platforms.
        Otherwise, return all enabled channels for the user.

        Returns:
            List of NotificationChannel objects.
        """
        from notifications.models import NotificationChannel

        user_channels = NotificationChannel.objects.filter(
            user=self.user,
            is_enabled=True,
        )

        override = self.notification_channels.get("channels")
        if override:
            return list(user_channels.filter(platform__in=override))

        return list(user_channels)

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


class PublicSearchPage(TimeStampedModel):
    """
    Admin-curated public search page accessible without authentication.

    Reuses existing Search infrastructure but adds:
    - Public-facing metadata (slug, title, description)
    - Scope limits (restrict which municipalities/states/dates users can filter to)
    - Search term locking (users cannot change the search term, only filters)

    Example: Admin creates /topics/rent/ with search_term="rent control OR tenant rights"
    Users can view results and filter within admin-defined boundaries.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Public-facing metadata
    slug = models.SlugField(
        max_length=100,
        unique=True,
        db_index=True,
        help_text="URL slug (e.g., 'rent' for /topics/rent/)",
    )
    title = models.CharField(
        max_length=200, help_text="Display title (e.g., 'Rent Control Discussions')"
    )
    description = models.TextField(
        blank=True,
        help_text="Optional description/intro text shown at top of page",
    )
    is_published = models.BooleanField(
        default=False,
        db_index=True,
        help_text="Only published pages are visible to public",
    )

    # The core search configuration (reuses Search model)
    search = models.ForeignKey(
        Search,
        on_delete=models.CASCADE,
        related_name="public_pages",
        help_text="The search configuration for this public page",
    )

    # Search term locking
    lock_search_term = models.BooleanField(
        default=True,
        help_text="If true, users cannot modify the search term (only filters)",
    )

    # Admin-Configurable Scope Limits
    # If set, users can ONLY filter within these boundaries
    allowed_municipalities = models.ManyToManyField(
        Muni,
        related_name="public_search_pages",
        blank=True,
        help_text="Limit users to only these municipalities (empty = all allowed)",
    )
    allowed_states = models.JSONField(
        default=list,
        blank=True,
        help_text="Limit users to only these states (empty = all allowed)",
    )
    min_date = models.DateField(
        null=True,
        blank=True,
        help_text="Users cannot search before this date",
    )
    max_date = models.DateField(
        null=True,
        blank=True,
        help_text="Users cannot search after this date",
    )

    # Metadata
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="created_public_searches",
    )
    view_count = models.IntegerField(
        default=0, help_text="Number of times this page has been viewed"
    )

    class Meta:
        verbose_name = "Public Search Page"
        verbose_name_plural = "Public Search Pages"
        ordering = ["title"]
        indexes = [
            models.Index(fields=["is_published", "slug"]),
        ]

    def __str__(self) -> str:
        status = "✓" if self.is_published else "✗"
        return f"{status} {self.title} (/topics/{self.slug}/)"

    def get_absolute_url(self) -> str:
        return reverse("searches:public-search-detail", kwargs={"slug": self.slug})

    def get_scope_description(self) -> str:
        """Human-readable description of admin-set scope limits."""
        parts = []
        if self.allowed_municipalities.exists():
            count = self.allowed_municipalities.count()
            parts.append(f"{count} municipalities")
        if self.allowed_states:
            parts.append(f"states: {', '.join(self.allowed_states)}")
        if self.min_date or self.max_date:
            date_range = (
                f"{self.min_date or 'beginning'} to {self.max_date or 'present'}"
            )
            parts.append(f"dates: {date_range}")
        return " | ".join(parts) if parts else "No scope limits"
