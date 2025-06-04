from django.contrib import admin

from .models import Muni


@admin.register(Muni)
class MuniAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "state",
        "country",
        "kind",
        "pages",
        "last_updated",
        "created",
    ]
    list_filter = ["state", "country", "kind", "created", "last_updated"]
    search_fields = ["name", "subdomain", "state"]
    readonly_fields = ["id", "created", "modified"]
    ordering = ["name"]

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
