import random
from datetime import timedelta

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html

from .models import SavedSearch, Search

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
    ]

    @admin.action(description="Populate selected searches with test results")
    def populate_test_results(self, request, queryset):
        """Admin action to populate selected searches with test result data."""
        updated_count = 0

        for search in queryset:
            self._populate_search_with_test_data(search)
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
            search.agenda_match_json = None
            search.minutes_match_json = None
            search.last_agenda_matched = None
            search.last_minutes_matched = None
            search.save()
            updated_count += 1

        self.message_user(
            request, f"Successfully cleared results from {updated_count} searches."
        )

    def _populate_search_with_test_data(self, search):
        """Populate a search with realistic test data."""
        now = timezone.now()

        # Generate agenda matches
        agenda_matches = []
        agenda_count = random.randint(0, 15)

        meeting_types = [
            "City Council",
            "Planning Commission",
            "Park Board",
            "School Board",
            "Water Board",
        ]

        for i in range(agenda_count):
            # Generate dates in the future for agendas
            future_date = now + timedelta(days=random.randint(1, 90))
            date_str = future_date.strftime("%Y-%m-%d")

            if search.all_results:
                # For "all results" searches, simpler structure
                agenda_matches.append(
                    {
                        "meeting": random.choice(meeting_types),
                        "date": date_str,
                        "count(page)": random.randint(1, 10),
                    }
                )
            else:
                # For keyword searches, detailed structure
                meeting_name = random.choice(meeting_types)
                agenda_matches.append(
                    {
                        "id": f"agenda-{i}-{search.id}",
                        "meeting": meeting_name,
                        "date": date_str,
                        "page": random.randint(1, 50),
                        "text": self._generate_realistic_text(
                            search.search_term, meeting_name
                        ),
                        "page_image": f"https://example.com/page-{i}.png",
                    }
                )

        # Generate minutes matches
        minutes_matches = []
        minutes_count = random.randint(0, 12)

        for i in range(minutes_count):
            # Generate dates in the past for minutes
            past_date = now - timedelta(days=random.randint(1, 180))
            date_str = past_date.strftime("%Y-%m-%d")

            if search.all_results:
                # For "all results" searches, simpler structure
                minutes_matches.append(
                    {
                        "meeting": random.choice(meeting_types),
                        "date": date_str,
                        "count(page)": random.randint(1, 15),
                    }
                )
            else:
                # For keyword searches, detailed structure
                meeting_name = random.choice(meeting_types)
                minutes_matches.append(
                    {
                        "id": f"minutes-{i}-{search.id}",
                        "meeting": meeting_name,
                        "date": date_str,
                        "page": random.randint(1, 75),
                        "text": self._generate_realistic_text(
                            search.search_term, meeting_name
                        ),
                        "page_image": f"https://example.com/minutes-page-{i}.png",
                    }
                )

        # Update the search with results
        search.agenda_match_json = agenda_matches if agenda_matches else None
        search.minutes_match_json = minutes_matches if minutes_matches else None
        search.last_fetched = now

        if agenda_matches:
            search.last_agenda_matched = now
        if minutes_matches:
            search.last_minutes_matched = now

        search.save()

    def _generate_realistic_text(self, search_term, meeting_name):
        """Generate realistic meeting text that contains the search term."""
        templates = [
            f"The committee discussed the {search_term} proposal submitted by staff members.",
            f"Motion to approve the {search_term} allocation for the upcoming fiscal year.",
            f"Public comment regarding the {search_term} project raised several concerns.",
            f"Staff recommendation on {search_term} was presented to the {meeting_name}.",
            f"The {search_term} initiative will be reviewed at the next meeting.",
            f"Council member Smith raised questions about the {search_term} implementation.",
            f"The {search_term} ordinance was passed with a 4-1 vote.",
            f"Discussion of {search_term} timeline and budget constraints.",
            f"Community feedback on the {search_term} proposal was overwhelmingly positive.",
            f"The {search_term} committee will report back next month.",
        ]

        if not search_term:
            # For "all results" searches, generate generic meeting text
            generic_templates = [
                f"The {meeting_name} convened to discuss various municipal matters.",
                f"Regular business was conducted by the {meeting_name}.",
                "Several agenda items were addressed during the meeting.",
                "Public participation was encouraged during the session.",
                "The meeting concluded with administrative updates.",
            ]
            return random.choice(generic_templates)

        return random.choice(templates)


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

        # Sample saved search name templates
        search_name_templates = [
            "{search_type} in {muni}",
            "Monitor {search_type} - {muni}",
            "{muni} {search_type} Watch",
            "Weekly {search_type} Updates",
            "{search_type} Notifications",
            "Track {search_type} Changes",
            "{muni} Municipal {search_type}",
        ]

        created_count = 0

        # Create 10 random saved searches
        for _ in range(10):
            user = random.choice(users)
            search = random.choice(searches)

            # Generate a descriptive name
            if search.search_term:
                search_type = f'"{search.search_term}"'
            else:
                search_type = "All Results"

            name_template = random.choice(search_name_templates)
            name = name_template.format(
                search_type=search_type,
                muni=search.muni.name,
            )

            # Create saved search (avoid duplicates with get_or_create)
            saved_search, created = SavedSearch.objects.get_or_create(
                user=user,
                search=search,
                defaults={"name": name},
            )

            if created:
                created_count += 1

                # Randomly set some notification timestamps to simulate activity
                if random.choice([True, False]):
                    # Simulate past notifications
                    days_ago = random.randint(1, 30)
                    saved_search.last_notification_sent = timezone.now() - timedelta(
                        days=days_ago
                    )
                    saved_search.save(update_fields=["last_notification_sent"])

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
                self._populate_search_with_test_data(search)
                searches_updated.add(search.id)
                updated_count += 1

        self.message_user(
            request,
            f"Successfully populated {updated_count} unique searches with test results from {queryset.count()} saved searches.",
        )

    def _populate_search_with_test_data(self, search):
        """Populate a search with realistic test data (reused from SearchAdmin)."""
        now = timezone.now()

        # Generate agenda matches
        agenda_matches = []
        agenda_count = random.randint(0, 15)

        meeting_types = [
            "City Council",
            "Planning Commission",
            "Park Board",
            "School Board",
            "Water Board",
        ]

        for i in range(agenda_count):
            # Generate dates in the future for agendas
            future_date = now + timedelta(days=random.randint(1, 90))
            date_str = future_date.strftime("%Y-%m-%d")

            if search.all_results:
                # For "all results" searches, simpler structure
                agenda_matches.append(
                    {
                        "meeting": random.choice(meeting_types),
                        "date": date_str,
                        "count(page)": random.randint(1, 10),
                    }
                )
            else:
                # For keyword searches, detailed structure
                meeting_name = random.choice(meeting_types)
                agenda_matches.append(
                    {
                        "id": f"agenda-{i}-{search.id}",
                        "meeting": meeting_name,
                        "date": date_str,
                        "page": random.randint(1, 50),
                        "text": self._generate_realistic_text(
                            search.search_term, meeting_name
                        ),
                        "page_image": f"https://example.com/page-{i}.png",
                    }
                )

        # Generate minutes matches
        minutes_matches = []
        minutes_count = random.randint(0, 12)

        for i in range(minutes_count):
            # Generate dates in the past for minutes
            past_date = now - timedelta(days=random.randint(1, 180))
            date_str = past_date.strftime("%Y-%m-%d")

            if search.all_results:
                # For "all results" searches, simpler structure
                minutes_matches.append(
                    {
                        "meeting": random.choice(meeting_types),
                        "date": date_str,
                        "count(page)": random.randint(1, 15),
                    }
                )
            else:
                # For keyword searches, detailed structure
                meeting_name = random.choice(meeting_types)
                minutes_matches.append(
                    {
                        "id": f"minutes-{i}-{search.id}",
                        "meeting": meeting_name,
                        "date": date_str,
                        "page": random.randint(1, 75),
                        "text": self._generate_realistic_text(
                            search.search_term, meeting_name
                        ),
                        "page_image": f"https://example.com/minutes-page-{i}.png",
                    }
                )

        # Update the search with results
        search.agenda_match_json = agenda_matches if agenda_matches else None
        search.minutes_match_json = minutes_matches if minutes_matches else None
        search.last_fetched = now

        if agenda_matches:
            search.last_agenda_matched = now
        if minutes_matches:
            search.last_minutes_matched = now

        search.save()

    def _generate_realistic_text(self, search_term, meeting_name):
        """Generate realistic meeting text that contains the search term."""
        templates = [
            f"The committee discussed the {search_term} proposal submitted by staff members.",
            f"Motion to approve the {search_term} allocation for the upcoming fiscal year.",
            f"Public comment regarding the {search_term} project raised several concerns.",
            f"Staff recommendation on {search_term} was presented to the {meeting_name}.",
            f"The {search_term} initiative will be reviewed at the next meeting.",
            f"Council member Smith raised questions about the {search_term} implementation.",
            f"The {search_term} ordinance was passed with a 4-1 vote.",
            f"Discussion of {search_term} timeline and budget constraints.",
            f"Community feedback on the {search_term} proposal was overwhelmingly positive.",
            f"The {search_term} committee will report back next month.",
        ]

        if not search_term:
            # For "all results" searches, generate generic meeting text
            generic_templates = [
                f"The {meeting_name} convened to discuss various municipal matters.",
                f"Regular business was conducted by the {meeting_name}.",
                "Several agenda items were addressed during the meeting.",
                "Public participation was encouraged during the session.",
                "The meeting concluded with administrative updates.",
            ]
            return random.choice(generic_templates)

        return random.choice(templates)
