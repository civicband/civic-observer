from django.contrib import admin

from .models import Notebook, NotebookEntry, Tag


@admin.register(Notebook)
class NotebookAdmin(admin.ModelAdmin):
    list_display = ["name", "user", "is_archived", "created", "modified"]
    list_filter = ["is_archived", "created"]
    search_fields = ["name", "user__email"]
    raw_id_fields = ["user"]


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ["name", "user", "created"]
    list_filter = ["created"]
    search_fields = ["name", "user__email"]
    raw_id_fields = ["user"]


@admin.register(NotebookEntry)
class NotebookEntryAdmin(admin.ModelAdmin):
    list_display = ["notebook", "meeting_page", "created"]
    list_filter = ["created", "notebook"]
    search_fields = ["notebook__name", "note"]
    raw_id_fields = ["notebook", "meeting_page"]
    filter_horizontal = ["tags"]
