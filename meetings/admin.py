from django.contrib import admin
from django.utils.html import format_html

from .models import BackfillJob, BackfillProgress, MeetingDocument, MeetingPage
from .utils import truncate_text


class MeetingPageInline(admin.TabularInline):
    """Inline admin for meeting pages."""

    model = MeetingPage
    extra = 0
    fields = ["page_number", "text", "page_image"]
    readonly_fields = ["page_number", "text", "page_image"]
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(MeetingDocument)
class MeetingDocumentAdmin(admin.ModelAdmin):
    """Admin interface for Meeting Documents."""

    list_display = [
        "meeting_name",
        "municipality",
        "meeting_date",
        "document_type",
        "page_count",
        "created",
        "modified",
    ]
    list_filter = [
        "document_type",
        "meeting_date",
        "municipality__state",
        "municipality__country",
    ]
    search_fields = [
        "meeting_name",
        "municipality__name",
        "municipality__subdomain",
    ]
    readonly_fields = ["id", "created", "modified"]
    date_hierarchy = "meeting_date"
    ordering = ["-meeting_date", "meeting_name"]

    fieldsets = [
        (
            "Meeting Information",
            {
                "fields": [
                    "municipality",
                    "meeting_name",
                    "meeting_date",
                    "document_type",
                ]
            },
        ),
        (
            "Metadata",
            {
                "fields": ["id", "created", "modified"],
                "classes": ["collapse"],
            },
        ),
    ]

    inlines = [MeetingPageInline]

    @admin.display(description="Pages")
    def page_count(self, obj):
        """Display the number of pages in this document."""
        return obj.pages.count()


@admin.register(MeetingPage)
class MeetingPageAdmin(admin.ModelAdmin):
    """Admin interface for Meeting Pages."""

    list_display = [
        "id",
        "document",
        "page_number",
        "text_preview",
        "created",
    ]
    list_filter = [
        "document__document_type",
        "document__municipality__state",
    ]
    search_fields = [
        "text",
        "document__meeting_name",
        "document__municipality__name",
    ]
    readonly_fields = ["id", "created", "modified"]
    ordering = ["document", "page_number"]

    fieldsets = [
        (
            "Page Information",
            {
                "fields": [
                    "id",
                    "document",
                    "page_number",
                    "page_image",
                ]
            },
        ),
        (
            "Content",
            {
                "fields": ["text"],
            },
        ),
        (
            "Metadata",
            {
                "fields": ["created", "modified"],
                "classes": ["collapse"],
            },
        ),
    ]

    @admin.display(description="Text Preview")
    def text_preview(self, obj):
        """Display a preview of the page text."""
        if obj.text:
            return truncate_text(obj.text, max_length=100)
        return "(empty)"


@admin.register(BackfillJob)
class BackfillJobAdmin(admin.ModelAdmin):
    """Admin interface for BackfillJob model."""

    list_display = [
        "id",
        "municipality",
        "document_type",
        "status",
        "pages_created",
        "pages_updated",
        "errors_encountered",
        "created",
        "verified_at",
    ]
    list_filter = ["status", "document_type", "created"]
    search_fields = ["municipality__subdomain", "municipality__name"]
    readonly_fields = [
        "id",
        "created",
        "modified",
        "verified_at",
        "pages_fetched",
        "pages_created",
        "pages_updated",
        "errors_encountered",
        "expected_count",
        "actual_count",
    ]
    fieldsets = [
        (
            "Job Info",
            {
                "fields": [
                    "id",
                    "municipality",
                    "document_type",
                    "status",
                    "created",
                    "modified",
                ]
            },
        ),
        (
            "Progress",
            {
                "fields": [
                    "last_cursor",
                    "pages_fetched",
                    "pages_created",
                    "pages_updated",
                    "errors_encountered",
                ]
            },
        ),
        (
            "Verification",
            {
                "fields": [
                    "expected_count",
                    "actual_count",
                    "verified_at",
                ]
            },
        ),
        (
            "Errors",
            {
                "fields": [
                    "last_error",
                    "retry_count",
                ]
            },
        ),
    ]


@admin.register(BackfillProgress)
class BackfillProgressAdmin(admin.ModelAdmin):
    """Admin interface for BackfillProgress model."""

    list_display = [
        "municipality_name",
        "document_type",
        "mode",
        "status_badge",
        "updated_at",
        "has_error",
    ]
    list_filter = ["status", "mode", "document_type"]
    search_fields = ["municipality__name", "municipality__subdomain"]
    readonly_fields = [
        "started_at",
        "updated_at",
        "error_message_display",
    ]
    fieldsets = [
        (
            "Backfill Information",
            {
                "fields": [
                    "municipality",
                    "document_type",
                    "mode",
                    "status",
                ]
            },
        ),
        (
            "Progress Tracking",
            {
                "fields": [
                    "next_cursor",
                    "force_full_backfill",
                    "started_at",
                    "updated_at",
                ]
            },
        ),
        (
            "Error Information",
            {
                "fields": ["error_message_display"],
                "classes": ["collapse"],
            },
        ),
    ]
    actions = ["force_full_backfill_action", "retry_failed_action"]

    @admin.display(description="Municipality")
    def municipality_name(self, obj):
        return f"{obj.municipality.name} ({obj.municipality.subdomain})"

    @admin.display(description="Status", ordering="status")
    def status_badge(self, obj):
        colors = {
            "pending": "gray",
            "in_progress": "blue",
            "completed": "green",
            "failed": "red",
        }
        color = colors.get(obj.status, "gray")
        return format_html(
            '<span style="color: {}; font-weight: bold;">{}</span>',
            color,
            obj.get_status_display(),
        )

    @admin.display(description="Has Error", boolean=True)
    def has_error(self, obj):
        return bool(obj.error_message)

    @admin.display(description="Error Message")
    def error_message_display(self, obj):
        if obj.error_message:
            return format_html(
                '<pre style="white-space: pre-wrap;">{}</pre>',
                obj.error_message,
            )
        return "No errors"

    @admin.action(description="Force full backfill on next run")
    def force_full_backfill_action(self, request, queryset):
        """Set force_full_backfill flag and trigger backfill."""
        import django_rq

        from meetings.tasks import backfill_municipality_meetings_task

        queue = django_rq.get_queue("default")
        count = 0

        for progress in queryset:
            progress.force_full_backfill = True
            progress.save()

            # Trigger backfill
            queue.enqueue(
                backfill_municipality_meetings_task,
                progress.municipality.id,
            )
            count += 1

        self.message_user(
            request,
            f"Triggered full backfill for {count} progress record(s).",
        )

    @admin.action(description="Retry failed backfill")
    def retry_failed_action(self, request, queryset):
        """Reset failed backfills to in_progress and re-enqueue."""
        import django_rq

        from meetings.tasks import backfill_batch_task, backfill_incremental_task

        queue = django_rq.get_queue("default")
        count = 0

        for progress in queryset.filter(status="failed"):
            progress.status = "in_progress"
            progress.error_message = None
            progress.save()

            # Enqueue appropriate task based on mode
            if progress.mode == "full":
                task = backfill_batch_task
            else:
                task = backfill_incremental_task

            queue.enqueue(
                task,
                progress.municipality.id,
                progress.document_type,
                progress.id,
            )
            count += 1

        self.message_user(
            request,
            f"Retried {count} failed backfill(s).",
        )
