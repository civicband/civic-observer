from django.contrib import admin
from django.db.models import Count

from .models import Muni


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
