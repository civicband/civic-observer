from django.contrib import admin
from django.utils import timezone

from .models import (
    IngestRun,
    MeetingHit,
    Mention,
    MutedCountry,
    Place,
    PlaceAlias,
    PlaceMention,
    PlaceNote,
    Subreddit,
    Thread,
    ThreadPlace,
)


class PlaceNeedsReviewFilter(admin.SimpleListFilter):
    title = "Needs review"
    parameter_name = "needs_review"

    def lookups(self, request, model_admin):
        return (("1", "Needs review"),)

    def queryset(self, request, queryset):
        if self.value() == "1":
            return queryset.filter(
                muni_link_status__in=[
                    Place.MuniLinkStatus.UNLINKED,
                    Place.MuniLinkStatus.NO_MATCH,
                ]
            )
        return queryset


class SubredditNeedsReviewFilter(admin.SimpleListFilter):
    title = "Needs review"
    parameter_name = "needs_review"

    def lookups(self, request, model_admin):
        return (("1", "Needs review"),)

    def queryset(self, request, queryset):
        if self.value() == "1":
            return queryset.filter(reviewed_at__isnull=True)
        return queryset


@admin.register(Place)
class PlaceAdmin(admin.ModelAdmin):
    list_display = [
        "canonical_key",
        "name",
        "place_type",
        "state",
        "country",
        "is_government",
        "muni_link_status",
        "population",
    ]
    list_filter = [
        "place_type",
        "state",
        "is_government",
        "muni_link_status",
        "country",
        PlaceNeedsReviewFilter,
    ]
    search_fields = ["name", "geoid", "canonical_key"]
    autocomplete_fields = ["county", "muni"]
    list_select_related = ["county", "muni"]


@admin.register(Subreddit)
class SubredditAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "kind",
        "place",
        "state",
        "country",
        "confidence",
        "source",
        "muted",
        "reviewed_at",
    ]
    list_filter = [
        "kind",
        "source",
        "muted",
        "confidence",
        "reviewed_at",
        SubredditNeedsReviewFilter,
    ]
    search_fields = ["name", "place__name", "note"]
    autocomplete_fields = ["place"]
    actions = ["accept_suggestions"]
    list_select_related = ["place"]

    @admin.action(description="Accept suggested classification")
    def accept_suggestions(self, request, queryset):
        now = timezone.now()
        queryset.update(reviewed_at=now, modified=now)


@admin.register(Thread)
class ThreadAdmin(admin.ModelAdmin):
    list_display = [
        "key",
        "title",
        "subreddit",
        "status",
        "mention_count",
        "last_seen",
    ]
    list_filter = ["status", "subreddit"]
    search_fields = ["key", "title", "subreddit__name"]
    list_select_related = ["subreddit"]


@admin.register(Mention)
class MentionAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "thread",
        "kind",
        "subreddit_name",
        "keyword",
        "author",
        "published_at",
    ]
    list_filter = ["kind", "site"]
    search_fields = ["id", "thread__key", "keyword", "author", "post_title"]
    list_select_related = ["thread"]
    readonly_fields = [field.name for field in Mention._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PlaceAlias)
class PlaceAliasAdmin(admin.ModelAdmin):
    list_display = ["place", "key", "kind", "source"]
    list_filter = ["kind", "source"]
    search_fields = ["key", "place__name"]
    list_select_related = ["place"]


@admin.register(ThreadPlace)
class ThreadPlaceAdmin(admin.ModelAdmin):
    list_display = ["thread", "place", "role", "confidence", "source"]
    list_filter = ["role", "confidence", "source"]
    search_fields = ["thread__key", "place__name"]
    list_select_related = ["thread", "place"]


@admin.register(PlaceMention)
class PlaceMentionAdmin(admin.ModelAdmin):
    list_display = [
        "mention",
        "place",
        "matched_text",
        "method",
        "confidence",
        "is_subject",
    ]
    list_filter = ["method", "confidence", "is_subject"]
    search_fields = ["matched_text", "place__name", "mention__id"]
    list_select_related = ["mention", "place"]


@admin.register(MeetingHit)
class MeetingHitAdmin(admin.ModelAdmin):
    list_display = [
        "thread",
        "place",
        "meeting_name",
        "meeting_date",
        "document_type",
        "rank",
    ]
    list_filter = ["document_type"]
    search_fields = ["thread__key", "place__name", "meeting_name", "query"]
    list_select_related = ["thread", "place"]


@admin.register(IngestRun)
class IngestRunAdmin(admin.ModelAdmin):
    list_display = [
        "started_at",
        "finished_at",
        "source",
        "pages",
        "seen",
        "new",
        "skipped",
        "error",
    ]
    search_fields = ["source"]
    readonly_fields = [field.name for field in IngestRun._meta.fields]
    ordering = ["-started_at"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PlaceNote)
class PlaceNoteAdmin(admin.ModelAdmin):
    list_display = ["place", "status", "text", "created"]
    list_filter = ["status"]
    search_fields = ["place__name", "text"]
    list_select_related = ["place"]


@admin.register(MutedCountry)
class MutedCountryAdmin(admin.ModelAdmin):
    list_display = ["code", "created"]
    search_fields = ["code"]
