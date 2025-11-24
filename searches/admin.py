from django.contrib import admin
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils.html import format_html

from .models import SavedSearch, Search
from .test_data_utils import TestDataGenerator

User = get_user_model()


@admin.register(Search)
class SearchAdmin(admin.ModelAdmin):
    list_display = [
        "get_municipalities_display",
        "search_term",
        "document_type",
        "last_result_count",
        "created",
    ]
    list_filter = ["document_type", "created"]
    search_fields = ["search_term"]
    readonly_fields = ["id", "created", "modified", "last_result_count"]
    ordering = ["-created"]
    filter_horizontal = ["municipalities"]

    fieldsets = [
        (
            "Search Filters",
            {
                "fields": [
                    "municipalities",
                    "search_term",
                    "states",
                    "date_from",
                    "date_to",
                    "document_type",
                    "meeting_name_query",
                ]
            },
        ),
        (
            "Results Tracking",
            {
                "fields": ["last_result_count", "last_result_page_ids", "last_fetched"],
                "classes": ["collapse"],
            },
        ),
        (
            "Timestamps",
            {"fields": ["id", "created", "modified"], "classes": ["collapse"]},
        ),
    ]

    @admin.display(description="Municipalities")
    def get_municipalities_display(self, obj):
        """Display municipalities for M2M relationship."""
        munis = obj.municipalities.all()[:3]
        muni_names = ", ".join(m.name for m in munis)
        if obj.municipalities.count() > 3:
            muni_names += f", +{obj.municipalities.count() - 3} more"
        return muni_names or "(none)"


@admin.register(SavedSearch)
class SavedSearchAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "user",
        "search",
        "notification_frequency",
        "has_pending_results",
        "created",
        "last_notification_sent",
        "preview_email",
    ]
    list_filter = [
        "notification_frequency",
        "has_pending_results",
        "created",
        "last_notification_sent",
    ]
    search_fields = ["name", "user__email", "search__search_term"]
    ordering = ["-created"]
    readonly_fields = [
        "id",
        "created",
        "modified",
        "last_checked",
        "last_notification_sent",
        "preview_email_links",
    ]
    # Temporarily removed test data actions until they're updated for new model structure
    # actions = ["create_test_saved_searches", "populate_search_results_for_selected"]

    fieldsets = [
        ("Saved Search", {"fields": ["name", "user", "search"]}),
        (
            "Notification Settings",
            {
                "fields": [
                    "notification_frequency",
                    "has_pending_results",
                    "last_notification_sent",
                    "last_checked",
                    "preview_email_links",
                ]
            },
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
