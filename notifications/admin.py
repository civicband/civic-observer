from django.contrib import admin

from .models import NotificationChannel


@admin.register(NotificationChannel)
class NotificationChannelAdmin(admin.ModelAdmin):
    list_display = [
        "user",
        "platform",
        "handle",
        "is_validated",
        "is_enabled",
        "failure_count",
        "last_used_at",
    ]
    list_filter = ["platform", "is_validated", "is_enabled"]
    search_fields = ["user__email", "handle"]
    readonly_fields = ["id", "created", "modified", "last_used_at"]
