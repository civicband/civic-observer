import logging

from django import forms
from django.contrib import admin
from django.db.models import Count

from .countries import to_iso_code
from .models import Muni

logger = logging.getLogger(__name__)


class MuniWatchLinkFilter(admin.SimpleListFilter):
    """Review surface for municipalities without a linked watch ``Place``.

    The watch app only creates ``Place`` rows for candidates it can classify,
    so a Muni that failed to match never appears in ``PlaceAdmin``'s review
    filter. Review those municipalities here instead.
    """

    title = "watch link"
    parameter_name = "watch_link"

    def lookups(self, request, model_admin):
        return (("linked", "Linked"), ("unlinked", "Unlinked"))

    def queryset(self, request, queryset):
        if self.value() == "linked":
            return queryset.filter(watch_places__isnull=False).distinct()
        if self.value() == "unlinked":
            return queryset.filter(watch_places__isnull=True)
        return queryset


class MuniAdminForm(forms.ModelForm):
    """Normalize free-text country input to an ISO 3166-1 alpha-2 code."""

    class Meta:
        model = Muni
        fields = "__all__"

    def clean_country(self):
        value = self.cleaned_data.get("country")
        country = to_iso_code(value.code if hasattr(value, "code") else value)
        if not country:
            raise forms.ValidationError(f"Unknown country: {value!r}")
        return country


@admin.register(Muni)
class MuniAdmin(admin.ModelAdmin):
    form = MuniAdminForm
    list_display = [
        "name",
        "state",
        "country",
        "kind",
        "pages",
        "saved_search_count",
        "last_updated",
        "last_indexed",
        "created",
    ]
    list_filter = [
        "state",
        "country",
        "kind",
        "created",
        "last_updated",
        "last_indexed",
        MuniWatchLinkFilter,
    ]
    search_fields = ["name", "subdomain", "state"]
    readonly_fields = ["id", "created", "modified", "last_indexed"]
    ordering = ["name"]
    actions = ["backfill_meetings"]

    # Enable sorting for all displayed columns
    sortable_by = [
        "name",
        "state",
        "country",
        "kind",
        "pages",
        "saved_search_count",
        "last_updated",
        "last_indexed",
    ]

    def get_queryset(self, request):
        """Add annotation for saved search count to enable sorting."""
        queryset = super().get_queryset(request)
        return queryset.annotate(
            saved_search_count=Count("searches__saved_by", distinct=True)
        )

    @admin.display(description="Saved Searches", ordering="saved_search_count")
    def saved_search_count(self, obj):
        """Display the number of saved searches for this municipality."""
        # Use the annotated value if available, otherwise compute it
        if hasattr(obj, "saved_search_count"):
            return obj.saved_search_count
        return obj.searches.aggregate(count=Count("saved_by", distinct=True))["count"]

    fieldsets = [
        (
            "Basic Information",
            {"fields": ["subdomain", "name", "state", "country", "kind"]},
        ),
        ("Data", {"fields": ["pages", "last_updated", "last_indexed"]}),
        ("Location", {"fields": ["latitude", "longitude"], "classes": ["collapse"]}),
        ("Additional Data", {"fields": ["popup_data"], "classes": ["collapse"]}),
        (
            "Timestamps",
            {"fields": ["id", "created", "modified"], "classes": ["collapse"]},
        ),
    ]

    @admin.action(description="Backfill meeting data from civic.band")
    def backfill_meetings(self, request, queryset):
        """Admin action to backfill meeting data for selected municipalities."""
        import django_rq

        from meetings.tasks import backfill_municipality_meetings_task

        enqueued_count = 0
        error_count = 0
        job_ids = []

        queue = django_rq.get_queue("default")

        for muni in queryset:
            try:
                job = queue.enqueue(backfill_municipality_meetings_task, muni.id)
                job_ids.append(job.id)
                enqueued_count += 1
                logger.info(
                    f"Enqueued backfill task for {muni.name} (job ID: {job.id})"
                )
            except Exception as e:
                error_count += 1
                logger.error(
                    f"Failed to enqueue backfill task for {muni.name}: {e}",
                    exc_info=True,
                )
                self.message_user(
                    request,
                    f"Failed to enqueue backfill task for {muni.name}: {str(e)}",
                    level="error",
                )

        # Summary message
        if enqueued_count > 0:
            self.message_user(
                request,
                f"Successfully enqueued backfill tasks for {enqueued_count} "
                f"municipalit{'y' if enqueued_count == 1 else 'ies'}. "
                f"Tasks will run in the background. Check logs for results.",
                level="success",
            )

        if error_count > 0:
            self.message_user(
                request,
                f"Failed to enqueue tasks for {error_count} "
                f"municipalit{'y' if error_count == 1 else 'ies'}.",
                level="error",
            )
