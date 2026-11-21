from datetime import timedelta

from django.contrib import admin, messages
from django.db.models import Exists, OuterRef, Q
from django.forms.models import BaseInlineFormSet
from django.utils import timezone

from .ingest import enqueue_enrichment
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
    ThreadLabel,
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


class CoveredFilter(admin.SimpleListFilter):
    title = "Covered"
    parameter_name = "covered"

    def lookups(self, request, model_admin):
        return (("yes", "Covered"), ("no", "Uncovered"))

    def queryset(self, request, queryset):
        covered = Exists(
            ThreadPlace.objects.filter(
                thread=OuterRef("pk"),
                role=ThreadPlace.Role.SUBJECT,
                place__muni__isnull=False,
            )
        )
        if self.value() == "yes":
            return queryset.filter(covered)
        if self.value() == "no":
            return queryset.exclude(covered)
        return queryset


class UsFilter(admin.SimpleListFilter):
    title = "US"
    parameter_name = "us"

    def lookups(self, request, model_admin):
        return (("yes", "US"), ("no", "Non-US"))

    def queryset(self, request, queryset):
        # Deliberately an OR: this treats a US place under a country-less
        # subreddit as US, unlike ``thread_country``'s subreddit-first
        # precedence.
        in_us = Q(subreddit__country="US") | Q(subreddit__place__country="US")
        if self.value() == "yes":
            return queryset.filter(in_us)
        if self.value() == "no":
            return queryset.exclude(in_us)
        return queryset


class HasLabelFilter(admin.SimpleListFilter):
    """Filter threads by whether they carry any operator label.

    A label with ``place=None`` still counts as labelled: it records the
    operator's judgement that the thread has no associated place.
    """

    title = "Has label"
    parameter_name = "has_label"

    def lookups(self, request, model_admin):
        return (("yes", "Labelled"), ("no", "Unlabelled"))

    def queryset(self, request, queryset):
        labelled = Exists(ThreadLabel.objects.filter(thread=OuterRef("pk")))
        if self.value() == "yes":
            return queryset.filter(labelled)
        if self.value() == "no":
            return queryset.exclude(labelled)
        return queryset


class ThreadLabelInlineFormSet(BaseInlineFormSet):
    """Validate label uniqueness before the database constraint fires."""

    def clean(self):
        super().clean()
        if any(self.errors):
            return

        seen: set[str | None] = set()
        for form in self.forms:
            if not form.cleaned_data or form.cleaned_data.get("DELETE"):
                continue
            # Empty extra forms are ignored by the formset's save logic.
            if form.instance.pk is None and not form.has_changed():
                continue
            place = form.cleaned_data.get("place")
            key = str(place.pk) if place else None
            if key in seen:
                form.add_error(
                    "place",
                    "This thread already has a label for this place."
                    if place
                    else "This thread already has a no-place label.",
                )
            else:
                seen.add(key)


class ThreadLabelInline(admin.TabularInline):
    model = ThreadLabel
    formset = ThreadLabelInlineFormSet
    extra = 0
    autocomplete_fields = ["place"]
    readonly_fields = ["created_at", "created_by"]


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
        "duplicate_of",
        "mention_count",
        "last_seen",
    ]
    list_filter = [
        "status",
        "subreddit__kind",
        CoveredFilter,
        UsFilter,
        HasLabelFilter,
        "subreddit__muted",
        "last_seen",
    ]
    search_fields = [
        "title",
        "mentions__snippet_text",
        "mentions__keyword",
        "subreddit__name",
    ]
    list_select_related = ["subreddit", "duplicate_of"]
    readonly_fields = ["duplicate_of"]
    inlines = [ThreadLabelInline]
    actions = [
        "mark_replied",
        "mark_dismissed",
        "snooze_seven_days",
        "send_to_backlog",
        "mute_subreddits",
        "rerun_enrichment",
    ]

    @admin.action(description="Mark selected threads as replied")
    def mark_replied(self, request, queryset):
        updated = self._set_status(queryset, Thread.Status.REPLIED)
        self.message_user(
            request,
            f"Marked {updated} thread(s) as replied.",
            messages.SUCCESS,
        )

    @admin.action(description="Mark selected threads as dismissed")
    def mark_dismissed(self, request, queryset):
        updated = self._set_status(queryset, Thread.Status.DISMISSED)
        self.message_user(
            request,
            f"Marked {updated} thread(s) as dismissed.",
            messages.SUCCESS,
        )

    @admin.action(description="Snooze selected threads for seven days")
    def snooze_seven_days(self, request, queryset):
        now = timezone.now()
        updated = queryset.update(
            status=Thread.Status.SNOOZED,
            snoozed_until=now + timedelta(days=7),
            modified=now,
        )
        self.message_user(
            request,
            f"Snoozed {updated} thread(s) for seven days.",
            messages.SUCCESS,
        )

    @admin.action(description="Send selected threads to the backlog")
    def send_to_backlog(self, request, queryset):
        updated = queryset.update(
            status=Thread.Status.BACKLOG,
            snoozed_until=None,
            modified=timezone.now(),
        )
        self.message_user(
            request,
            f"Sent {updated} thread(s) to the backlog.",
            messages.SUCCESS,
        )

    @admin.action(description="Mute subreddits for selected threads")
    def mute_subreddits(self, request, queryset):
        subreddit_ids = queryset.exclude(subreddit__isnull=True).values_list(
            "subreddit_id", flat=True
        )
        updated = Subreddit.objects.filter(pk__in=subreddit_ids, muted=False).update(
            muted=True, modified=timezone.now()
        )
        self.message_user(
            request,
            f"Muted {updated} subreddit(s).",
            messages.SUCCESS,
        )

    @admin.action(description="Rerun enrichment for selected threads")
    def rerun_enrichment(self, request, queryset):
        enqueued = 0
        failed = 0
        for thread_id in queryset.values_list("pk", flat=True):
            if enqueue_enrichment(str(thread_id)) is None:
                failed += 1
            else:
                enqueued += 1

        if enqueued:
            self.message_user(
                request,
                f"Enqueued enrichment for {enqueued} thread(s).",
                messages.SUCCESS,
            )
        if failed:
            self.message_user(
                request,
                f"Could not enqueue enrichment for {failed} thread(s); "
                "is the queue available?",
                messages.ERROR,
            )

    def _set_status(self, queryset, status: str) -> int:
        """Set a terminal status and drop any stale snooze timestamp."""
        return queryset.update(
            status=status,
            snoozed_until=None,
            modified=timezone.now(),
        )

    def save_formset(self, request, form, formset, change):
        """Attribute new inline labels to the operator who saved them."""
        if formset.model is not ThreadLabel:
            return super().save_formset(request, form, formset, change)

        instances = formset.save(commit=False)
        for obj in formset.deleted_objects:
            obj.delete()
        for instance in instances:
            if instance.created_by_id is None:
                instance.created_by = request.user
            instance.save()
        formset.save_m2m()


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


@admin.register(ThreadLabel)
class ThreadLabelAdmin(admin.ModelAdmin):
    list_display = ["thread", "place", "is_subject", "is_useful", "created_by"]
    list_filter = ["is_subject", "is_useful"]
    search_fields = ["thread__key", "thread__title", "place__name"]
    autocomplete_fields = ["thread", "place"]
    list_select_related = ["thread", "place", "created_by"]
    readonly_fields = ["created_by", "created_at"]

    def save_model(self, request, obj, form, change):
        """Attribute new labels to the operator who saved them."""
        if obj.created_by_id is None:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(ThreadPlace)
class ThreadPlaceAdmin(admin.ModelAdmin):
    list_display = ["thread", "place", "role", "confidence", "source"]
    list_filter = ["role", "confidence", "source"]
    search_fields = ["thread__key", "place__name"]
    list_select_related = ["thread", "place"]
    readonly_fields = ["source"]

    def save_model(self, request, obj, form, change):
        """Mark operator edits manual so the next enrichment preserves them."""
        obj.source = ThreadPlace.Source.MANUAL
        super().save_model(request, obj, form, change)


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
