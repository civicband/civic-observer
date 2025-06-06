from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html

from .models import SavedSearch, Search


@admin.register(Search)
class SearchAdmin(admin.ModelAdmin):
    list_display = ["muni", "search_term", "all_results", "created"]
    list_filter = ["all_results", "created", "muni__state", "muni__kind"]
    search_fields = ["search_term", "muni__name", "muni__subdomain"]
    readonly_fields = ["id", "created", "modified"]
    ordering = ["-created"]

    fieldsets = [
        ("Search Information", {"fields": ["muni", "search_term", "all_results"]}),
        (
            "Timestamps",
            {"fields": ["id", "created", "modified"], "classes": ["collapse"]},
        ),
    ]


@admin.register(SavedSearch)
class SavedSearchAdmin(admin.ModelAdmin):
    list_display = ["name", "user", "search", "created", "preview_email"]
    list_filter = ["created"]
    search_fields = ["name", "user__email", "search__search_term"]
    ordering = ["-created"]
    readonly_fields = ["id", "created", "modified", "preview_email_links"]

    fieldsets = [
        ("Saved Search", {"fields": ["name", "user", "search"]}),
        ("Email Preview", {"fields": ["preview_email_links"]}),
        (
            "Timestamps",
            {"fields": ["id", "created", "modified"], "classes": ["collapse"]},
        ),
    ]

    @admin.display(description="Email Preview")
    def preview_email(self, obj):
        """Add links to preview the email that would be sent"""
        html_url = reverse("searches:savedsearch-email-preview", kwargs={"pk": obj.pk})
        txt_url = reverse(
            "searches:savedsearch-email-preview-format",
            kwargs={"pk": obj.pk, "format": "txt"},
        )
        return format_html(
            '<a href="{}" target="_blank">HTML</a> | <a href="{}" target="_blank">Text</a>',
            html_url,
            txt_url,
        )

    @admin.display(description="Email Preview Links")
    def preview_email_links(self, obj):
        """Detailed email preview links for the detail view"""
        if not obj.pk or not obj._state.db:
            return "Save the search first to preview emails"

        html_url = reverse("searches:savedsearch-email-preview", kwargs={"pk": obj.pk})
        txt_url = reverse(
            "searches:savedsearch-email-preview-format",
            kwargs={"pk": obj.pk, "format": "txt"},
        )
        return format_html(
            "<p><strong>Preview the email that would be sent for this saved search:</strong></p>"
            "<ul>"
            '<li><a href="{}" target="_blank">View HTML Email</a></li>'
            '<li><a href="{}" target="_blank">View Plain Text Email</a></li>'
            "</ul>",
            html_url,
            txt_url,
        )
