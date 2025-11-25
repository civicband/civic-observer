from django.contrib import admin
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html

from .models import SavedSearch, Search
from .tasks import check_saved_search_for_updates

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
                "fields": [
                    "last_result_count",
                    "last_checked_for_new_pages",
                    "last_fetched",
                ],
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
    actions = [
        "check_for_new_results",
        "send_test_notification",
        "mark_as_pending",
        "clear_pending",
    ]

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

    # Admin Actions for Testing

    @admin.action(description="Check for new results and send notifications")
    def check_for_new_results(self, request, queryset):
        """
        Check selected saved searches for new results.
        - For immediate: Sends notification if new results found
        - For daily/weekly: Marks as having pending results
        """
        count = 0
        immediate_sent = 0
        pending_marked = 0

        for saved_search in queryset.select_related("search", "user"):
            check_saved_search_for_updates(saved_search.id)
            count += 1

            # Check if notification was sent or pending was marked
            saved_search.refresh_from_db()
            if saved_search.notification_frequency == "immediate":
                # Check if last_notification_sent was just updated (within last minute)
                if (
                    saved_search.last_notification_sent
                    and (
                        timezone.now() - saved_search.last_notification_sent
                    ).total_seconds()
                    < 60
                ):
                    immediate_sent += 1
            elif saved_search.has_pending_results:
                pending_marked += 1

        message_parts = [f"Checked {count} saved search(es)."]
        if immediate_sent:
            message_parts.append(f"Sent {immediate_sent} immediate notification(s).")
        if pending_marked:
            message_parts.append(
                f"Marked {pending_marked} search(es) as having pending results."
            )

        self.message_user(request, " ".join(message_parts))

    @admin.action(description="Send test notification (immediate only)")
    def send_test_notification(self, request, queryset):
        """
        Send a test notification for selected searches.
        Gets current results (not just new) and sends notification email.
        Useful for testing email templates and delivery.
        """
        count = 0
        for saved_search in queryset.select_related("search", "user"):
            # Get current results (all matching pages)
            from .services import execute_search

            current_results = execute_search(saved_search.search)

            if current_results.exists():
                # Send notification with current results
                saved_search.send_search_notification(new_pages=current_results[:10])
                count += 1
            else:
                self.message_user(
                    request,
                    f"No results found for '{saved_search.name}' - no email sent.",
                    level="warning",
                )

        if count:
            self.message_user(
                request, f"Sent {count} test notification(s) with current results."
            )

    @admin.action(description="Mark as having pending results (for testing digests)")
    def mark_as_pending(self, request, queryset):
        """
        Mark selected searches as having pending results.
        Useful for testing daily/weekly digest emails.
        """
        count = queryset.update(has_pending_results=True, last_checked=timezone.now())
        self.message_user(
            request,
            f"Marked {count} saved search(es) as having pending results. "
            "Use 'send_daily_digests' or 'send_weekly_digests' management commands to test.",
        )

    @admin.action(description="Clear pending results flag")
    def clear_pending(self, request, queryset):
        """Clear the pending results flag for selected searches."""
        count = queryset.update(has_pending_results=False)
        self.message_user(
            request, f"Cleared pending results for {count} saved search(es)."
        )
