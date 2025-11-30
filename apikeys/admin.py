from django.contrib import admin

from .models import APIKey


@admin.register(APIKey)
class APIKeyAdmin(admin.ModelAdmin):
    list_display = ["name", "prefix", "user", "is_active", "created", "last_used_at"]
    list_filter = ["is_active", "created"]
    search_fields = ["name", "prefix", "user__email"]
    readonly_fields = ["prefix", "key_hash", "created", "modified", "last_used_at"]
    raw_id_fields = ["user"]
