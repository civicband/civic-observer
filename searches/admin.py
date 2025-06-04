from django.contrib import admin

from .models import Search


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
