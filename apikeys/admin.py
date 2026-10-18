from django.contrib import admin

from .models import APIKey, APIKeySite


class APIKeySiteInline(admin.TabularInline):
    model = APIKeySite
    extra = 1
    autocomplete_fields = ["muni"]


@admin.register(APIKey)
class APIKeyAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "prefix",
        "user",
        "all_sites",
        "site_summary",
        "is_active",
        "created",
        "last_used_at",
    ]
    list_filter = ["is_active", "all_sites", "created"]
    search_fields = ["name", "prefix", "user__email"]
    readonly_fields = ["prefix", "key_hash", "created", "modified", "last_used_at"]
    raw_id_fields = ["user"]
    inlines = [APIKeySiteInline]

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("sites")

    @admin.display(description="Sites")
    def site_summary(self, obj):
        return obj.site_summary()
