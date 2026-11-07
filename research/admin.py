from django.contrib import admin

from .models import (
    AuditEvent,
    Code,
    Coding,
    IgnoreRule,
    Memo,
    Project,
    ResultSet,
    SetItem,
)


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ["name", "owner", "is_archived", "modified"]
    list_filter = ["is_archived"]
    raw_id_fields = ["owner"]


@admin.register(ResultSet)
class ResultSetAdmin(admin.ModelAdmin):
    list_display = ["name", "project", "origin", "status", "item_count", "modified"]
    list_filter = ["origin", "status"]
    raw_id_fields = ["project", "parent"]


@admin.register(SetItem)
class SetItemAdmin(admin.ModelAdmin):
    list_display = ["result_set", "page", "disposition", "added_via"]
    list_filter = ["disposition", "added_via"]
    raw_id_fields = ["result_set", "page", "last_event", "disposition_by_rule"]


@admin.register(Code)
class CodeAdmin(admin.ModelAdmin):
    list_display = ["name", "project", "parent", "is_archived"]
    raw_id_fields = ["project", "parent"]


@admin.register(Coding)
class CodingAdmin(admin.ModelAdmin):
    list_display = ["code", "page", "project", "is_exemplar"]
    raw_id_fields = ["project", "code", "page", "coder"]


@admin.register(Memo)
class MemoAdmin(admin.ModelAdmin):
    list_display = ["project", "author", "created"]
    raw_id_fields = ["project", "author", "result_set", "code", "page"]


@admin.register(IgnoreRule)
class IgnoreRuleAdmin(admin.ModelAdmin):
    list_display = ["name", "project", "is_active"]
    raw_id_fields = ["project"]


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = ["verb", "project", "result_set", "created"]
    list_filter = ["verb"]
    raw_id_fields = ["project", "actor", "result_set"]
