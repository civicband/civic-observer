from django.contrib import admin
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils.html import format_html

from .models import SavedSearch, Search
from .test_data_utils import TestDataGenerator

User = get_user_model()


@admin.register(Search)
class SearchAdmin(admin.ModelAdmin):
    list_display = ["muni", "search_term", "all_results", "created"]
    list_filter = ["all_results", "created", "muni__state", "muni__kind"]
    search_fields = ["search_term", "muni__name", "muni__subdomain"]
    readonly_fields = ["id", "created", "modified"]
    ordering = ["-created"]
    actions = ["populate_test_results", "clear_search_results"]

    fieldsets = [
        ("Search Information", {"fields": ["muni", "search_term", "all_results"]}),
        (
            "Timestamps",
            {"fields": ["id", "created", "modified"], "classes": ["collapse"]},
        ),
        (
            "Results",
            {
                "fields": ["agenda_match_json", "minutes_match_json"],
                "classes": ["collapse"],
            },
        ),
    ]

    @admin.action(description="Populate selected searches with test results")
    def populate_test_results(self, request, queryset):
        """Admin action to populate selected searches with test result data."""
        updated_count = 0

        for search in queryset:
            TestDataGenerator.populate_search_with_test_data(search)
            updated_count += 1

        self.message_user(
            request,
            f"Successfully populated {updated_count} searches with test results.",
        )

    @admin.action(description="Clear search results from selected searches")
    def clear_search_results(self, request, queryset):
        """Admin action to clear results from selected searches."""
        updated_count = 0

        for search in queryset:
            TestDataGenerator.clear_search_results(search)
            updated_count += 1

        self.message_user(
            request, f"Successfully cleared results from {updated_count} searches."
        )


@admin.register(SavedSearch)
class SavedSearchAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "user",
        "search",
        "created",
        "last_notification_sent",
        "preview_email",
    ]
    list_filter = ["created", "last_notification_sent"]
    search_fields = ["name", "user__email", "search__search_term"]
    ordering = ["-created"]
    readonly_fields = [
        "id",
        "created",
        "modified",
        "last_notification_sent",
        "preview_email_links",
    ]
    actions = ["create_test_saved_searches", "populate_search_results_for_selected"]

    fieldsets = [
        ("Saved Search", {"fields": ["name", "user", "search"]}),
        (
            "Email Notifications",
            {"fields": ["last_notification_sent", "preview_email_links"]},
        ),
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

    @admin.action(
        description="Create test saved searches for existing users and searches"
    )
    def create_test_saved_searches(self, request, queryset):
        """Admin action to create additional test saved searches."""
        from .models import Search

        # Get available users and searches
        users = list(User.objects.all())
        searches = list(Search.objects.all())

        if not users:
            self.message_user(
                request, "No users found. Create users first.", level="error"
            )
            return

        if not searches:
            self.message_user(
                request, "No searches found. Create searches first.", level="error"
            )
            return

        created_count = TestDataGenerator.create_test_saved_searches(10)

        self.message_user(
            request, f"Successfully created {created_count} new test saved searches."
        )

    @admin.action(
        description="Populate search results for searches linked to selected saved searches"
    )
    def populate_search_results_for_selected(self, request, queryset):
        """Admin action to populate the associated searches with test data."""
        updated_count = 0
        searches_updated = set()

        for saved_search in queryset:
            search = saved_search.search

            # Only update each search once, even if multiple saved searches reference it
            if search.id not in searches_updated:
                TestDataGenerator.populate_search_with_test_data(search)
                searches_updated.add(search.id)
                updated_count += 1

        self.message_user(
            request,
            f"Successfully populated {updated_count} unique searches with test results from {queryset.count()} saved searches.",
        )
