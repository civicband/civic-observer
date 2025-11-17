import logging

from django.contrib import admin
from django.db.models import Count

from .models import Muni

logger = logging.getLogger(__name__)


@admin.register(Muni)
class MuniAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "state",
        "country",
        "kind",
        "pages",
        "saved_search_count",
        "last_updated",
        "created",
    ]
    list_filter = ["state", "country", "kind", "created", "last_updated"]
    search_fields = ["name", "subdomain", "state"]
    readonly_fields = ["id", "created", "modified"]
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
        ("Data", {"fields": ["pages", "last_updated"]}),
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
        from meetings.services import backfill_municipality_meetings

        success_count = 0
        error_count = 0
        total_docs = 0
        total_pages = 0

        for muni in queryset:
            try:
                stats = backfill_municipality_meetings(muni)
                success_count += 1
                total_docs += stats["documents_created"] + stats["documents_updated"]
                total_pages += stats["pages_created"] + stats["pages_updated"]

                if stats["errors"] > 0:
                    self.message_user(
                        request,
                        f"Backfilled {muni.name} with {stats['errors']} errors",
                        level="warning",
                    )
            except Exception as e:
                error_count += 1
                logger.error(f"Failed to backfill {muni.name}: {e}", exc_info=True)
                self.message_user(
                    request,
                    f"Failed to backfill {muni.name}: {str(e)}",
                    level="error",
                )

        # Summary message
        if success_count > 0:
            self.message_user(
                request,
                f"Successfully backfilled {success_count} municipalit{'y' if success_count == 1 else 'ies'}. "
                f"Created/updated {total_docs} documents and {total_pages} pages.",
                level="success",
            )

        if error_count > 0:
            self.message_user(
                request,
                f"Failed to backfill {error_count} municipalit{'y' if error_count == 1 else 'ies'}.",
                level="error",
            )
