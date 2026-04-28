import datetime

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.mail import EmailMultiAlternatives
from django.template.loader import get_template, render_to_string
from django.utils import timezone

from .models import DigestSubscription

User = get_user_model()


class DigestSubscriptionInline(admin.TabularInline):
    model = DigestSubscription
    extra = 0
    raw_id_fields = ["municipality"]
    fields = ["municipality", "is_active", "last_digest_sent", "created", "modified"]
    readonly_fields = ["created", "modified"]


@admin.register(DigestSubscription)
class DigestSubscriptionAdmin(admin.ModelAdmin):
    list_display = [
        "user",
        "municipality",
        "is_active",
        "last_digest_sent",
        "created",
    ]
    list_filter = ["is_active", "municipality__state", "municipality__name"]
    search_fields = ["user__email", "municipality__name", "municipality__subdomain"]
    readonly_fields = ["id", "created", "modified", "last_digest_sent"]
    raw_id_fields = ["user", "municipality"]
    ordering = ["-created"]
    actions = [
        "send_test_digest",
        "send_test_digest_for_tomorrow",
        "reset_last_digest_sent",
        "mark_active",
        "mark_inactive",
    ]

    @admin.action(description="Send test digest for today")
    def send_test_digest(self, request, queryset):
        today = timezone.now().date()
        count = 0
        skipped = 0
        for sub in queryset.select_related("user", "municipality"):
            if self._send_test_email_for_date(sub, today):
                count += 1
            else:
                skipped += 1
        parts = [f"Sent {count} test digest(s) for today."]
        if skipped:
            parts.append(f"{skipped} skipped (no meetings for that date).")
        self.message_user(request, " ".join(parts))

    @admin.action(description="Send test digest for tomorrow")
    def send_test_digest_for_tomorrow(self, request, queryset):
        tomorrow = timezone.now().date() + datetime.timedelta(days=1)
        count = 0
        skipped = 0
        for sub in queryset.select_related("user", "municipality"):
            if self._send_test_email_for_date(sub, tomorrow):
                count += 1
            else:
                skipped += 1
        parts = [f"Sent {count} test digest(s) for tomorrow."]
        if skipped:
            parts.append(f"{skipped} skipped (no meetings for that date).")
        self.message_user(request, " ".join(parts))

    def _send_test_email_for_date(self, subscription, target_date) -> bool:
        from meetings.models import MeetingDocument

        meetings = (
            MeetingDocument.objects.filter(
                meeting_date=target_date,
                municipality=subscription.municipality,
            )
            .select_related("municipality")
            .order_by("meeting_name")
        )

        if not meetings.exists():
            return False

        context = {
            "user": subscription.user,
            "grouped_meetings": [(subscription.municipality, list(meetings))],
            "meeting_date": target_date,
        }

        txt_content = render_to_string("email/meeting_digest.txt", context=context)
        html_content = get_template("email/meeting_digest.html").render(context=context)

        msg = EmailMultiAlternatives(
            subject=f"TEST: Today's Civic Meetings — {target_date}",
            to=[subscription.user.email],
            from_email="Civic Observer <noreply@civic.observer>",
            body=txt_content,
        )
        msg.attach_alternative(html_content, "text/html")
        msg.esp_extra = {"MessageStream": "outbound"}  # type: ignore[attr-defined]
        msg.send()
        return True

    @admin.action(description="Reset last digest sent date")
    def reset_last_digest_sent(self, request, queryset):
        count = queryset.update(last_digest_sent=None)
        self.message_user(
            request, f"Reset last_digest_sent for {count} subscription(s)."
        )

    @admin.action(description="Mark selected subscriptions as active")
    def mark_active(self, request, queryset):
        count = queryset.update(is_active=True)
        self.message_user(request, f"Marked {count} subscription(s) as active.")

    @admin.action(description="Mark selected subscriptions as inactive")
    def mark_inactive(self, request, queryset):
        count = queryset.update(is_active=False)
        self.message_user(request, f"Marked {count} subscription(s) as inactive.")
