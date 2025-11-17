from django.contrib import admin

from .models import MeetingDocument, MeetingPage


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

    def page_count(self, obj):
        """Display the number of pages in this document."""
        return obj.pages.count()

    page_count.short_description = "Pages"


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

    def text_preview(self, obj):
        """Display a preview of the page text."""
        if obj.text:
            return obj.text[:100] + "..." if len(obj.text) > 100 else obj.text
        return "(empty)"

    text_preview.short_description = "Text Preview"
