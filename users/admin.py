from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ["email", "username", "first_name", "last_name", "is_staff"]
    list_filter = ["is_staff", "is_superuser", "is_active", "date_joined"]
    search_fields = ["email", "username", "first_name", "last_name"]
    ordering = ["email"]
