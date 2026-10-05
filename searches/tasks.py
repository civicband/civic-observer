"""
Background tasks for saved search notifications.

These tasks handle checking saved searches and sending notifications when
new matching pages are found.
"""

import logging

from django.db.models import Q
from django.utils import timezone

from .models import SavedSearch

logger = logging.getLogger(__name__)


def check_saved_search_for_updates(saved_search_id) -> dict[str, str | int]:
    """
    Check a single saved search for new results and send notification if needed.

    Uses this saved search's own ``last_checked_for_new_pages`` cutoff, so
    multiple users sharing one Search are each notified independently and one
    user's check cannot consume new pages on another user's behalf.

    Args:
        saved_search_id: ID of the SavedSearch to check

    Returns:
        Dict with status information:
        - status: "not_found" | "no_new_results" | "notified" | "pending"
        - saved_search_id: The ID that was checked
        - new_results_count: Number of new results (if applicable)
        - action: Description of action taken
    """
    from .services import search_new_pages

    try:
        saved_search = SavedSearch.objects.select_related("search", "user").get(
            id=saved_search_id
        )
    except SavedSearch.DoesNotExist:
        logger.error(f"SavedSearch {saved_search_id} not found")
        return {
            "status": "not_found",
            "saved_search_id": str(saved_search_id),
            "action": "SavedSearch not found in database",
        }

    search = saved_search.search
    new_pages, total = search_new_pages(search, saved_search.last_checked_for_new_pages)

    # Advance this saved search's own cutoff, and refresh Search-level tracking.
    now = timezone.now()
    saved_search.last_checked_for_new_pages = now
    saved_search.last_checked = now
    saved_search.save(
        update_fields=["last_checked_for_new_pages", "last_checked", "modified"]
    )
    search.last_result_count = total
    search.last_fetched = now
    search.save(update_fields=["last_result_count", "last_fetched", "modified"])

    # If no new results, nothing to do
    if not new_pages.exists():
        logger.debug(
            f"No new results for SavedSearch {saved_search.id} ({saved_search.name})"
        )
        return {
            "status": "no_new_results",
            "saved_search_id": str(saved_search.id),
            "new_results_count": 0,
            "action": "No new results found",
        }

    new_results_count = new_pages.count()
    logger.info(
        f"Found {new_results_count} new results for SavedSearch {saved_search.id} ({saved_search.name})"
    )

    # Handle based on notification frequency
    if saved_search.notification_frequency == "immediate":
        # Send to additional notification channels
        _send_to_notification_channels(saved_search, new_pages)

        # Send email notification (always - fallback)
        saved_search.send_search_notification(new_pages=new_pages)
        logger.info(
            f"Sent immediate notification for SavedSearch {saved_search.id} to {saved_search.user.email}"
        )
        return {
            "status": "notified",
            "saved_search_id": str(saved_search.id),
            "new_results_count": new_results_count,
            "action": f"Sent immediate notification to {saved_search.user.email}",
        }
    else:
        # Flag for digest notification
        saved_search.has_pending_results = True
        saved_search.save(update_fields=["has_pending_results"])
        logger.info(
            f"Flagged SavedSearch {saved_search.id} for {saved_search.notification_frequency} digest"
        )
        return {
            "status": "pending",
            "saved_search_id": str(saved_search.id),
            "new_results_count": new_results_count,
            "action": f"Marked for {saved_search.notification_frequency} digest",
        }


def check_saved_searches(municipality_id=None) -> dict[str, int]:
    """
    Check all saved searches for new results after content is ingested.

    Immediate-frequency searches send a notification; daily and weekly searches
    are flagged with ``has_pending_results`` so the next digest includes them.
    This should be called after new pages are ingested (e.g., from webhook or
    backfill).

    Args:
        municipality_id: If provided, only checks saved searches scoped to this
            municipality (plus all-municipality searches). If None, checks every
            saved search.

    Returns:
        Dict with statistics:
        - searches_checked: Total number of saved searches checked
        - emails_sent: Number of immediate notification emails sent
        - pending_marked: Number of digest searches flagged with pending results
        - errors: Number of errors encountered
    """
    saved_searches = SavedSearch.objects.select_related("search", "user")
    if municipality_id is not None:
        saved_searches = saved_searches.filter(
            Q(search__municipalities__id=municipality_id)
            | Q(search__municipalities__isnull=True)
        ).distinct()

    total_count = saved_searches.count()
    logger.info(f"Checking {total_count} saved searches after ingest")

    emails_sent = 0
    pending_marked = 0
    errors = 0

    for saved_search in saved_searches:
        result = check_saved_search_for_updates(saved_search.id)
        if result["status"] == "notified":
            emails_sent += 1
        elif result["status"] == "pending":
            pending_marked += 1
        elif result["status"] == "not_found":
            errors += 1

    logger.info(
        f"Checked {total_count} saved searches: {emails_sent} emails sent, "
        f"{pending_marked} flagged for digest"
    )

    return {
        "searches_checked": total_count,
        "emails_sent": emails_sent,
        "pending_marked": pending_marked,
        "errors": errors,
    }


def send_daily_digests() -> dict[str, int]:
    """
    Send daily digest emails to users with pending results.

    This should be scheduled to run once per day (e.g., via cron or django-rq-scheduler).
    It groups saved searches by user and sends one email per user containing all their
    pending daily digest searches.

    Returns:
        Dict with statistics:
        - emails_sent: Number of digest emails sent (one per user)
        - searches_notified: Total number of saved searches included
    """
    from collections import defaultdict

    # Get all daily saved searches with pending results
    daily_searches = SavedSearch.objects.filter(
        notification_frequency="daily", has_pending_results=True
    ).select_related("search", "user")

    total_searches = daily_searches.count()
    logger.info(f"Sending daily digests for {total_searches} saved searches")

    # Group by user
    searches_by_user = defaultdict(list)
    for saved_search in daily_searches:
        searches_by_user[saved_search.user].append(saved_search)

    # Send one email per user
    emails_sent = 0
    for user, user_searches in searches_by_user.items():
        if _send_digest_email(user, user_searches, frequency="daily"):
            emails_sent += 1
            logger.info(
                f"Sent daily digest to {user.email} with {len(user_searches)} saved searches"
            )

    logger.info(
        f"Daily digest complete: {emails_sent} emails sent for {total_searches} searches"
    )

    return {"emails_sent": emails_sent, "searches_notified": total_searches}


def send_weekly_digests() -> dict[str, int]:
    """
    Send weekly digest emails to users with pending results.

    This should be scheduled to run once per week (e.g., via cron or django-rq-scheduler).
    It groups saved searches by user and sends one email per user containing all their
    pending weekly digest searches.

    Returns:
        Dict with statistics:
        - emails_sent: Number of digest emails sent (one per user)
        - searches_notified: Total number of saved searches included
    """
    from collections import defaultdict

    # Get all weekly saved searches with pending results
    weekly_searches = SavedSearch.objects.filter(
        notification_frequency="weekly", has_pending_results=True
    ).select_related("search", "user")

    total_searches = weekly_searches.count()
    logger.info(f"Sending weekly digests for {total_searches} saved searches")

    # Group by user
    searches_by_user = defaultdict(list)
    for saved_search in weekly_searches:
        searches_by_user[saved_search.user].append(saved_search)

    # Send one email per user
    emails_sent = 0
    for user, user_searches in searches_by_user.items():
        if _send_digest_email(user, user_searches, frequency="weekly"):
            emails_sent += 1
            logger.info(
                f"Sent weekly digest to {user.email} with {len(user_searches)} saved searches"
            )

    logger.info(
        f"Weekly digest complete: {emails_sent} emails sent for {total_searches} searches"
    )

    return {"emails_sent": emails_sent, "searches_notified": total_searches}


def _send_digest_email(user, saved_searches, frequency="daily") -> bool:
    """
    Send a digest email for multiple saved searches.

    Args:
        user: User to send email to
        saved_searches: List of SavedSearch objects with pending results
        frequency: "daily" or "weekly"

    Returns:
        True if the email was sent, False if sending failed (the searches are
        re-flagged as pending so the next run retries them).

    The pending flag is cleared *before* sending: an SMTP send can't be rolled
    back, so committing the state first means a crash can at worst drop one
    digest, never resend it. If the send fails, the searches are re-flagged.
    """
    from django.core.mail import EmailMultiAlternatives
    from django.template.loader import get_template, render_to_string

    # Prepare context with all saved searches
    context = {
        "user": user,
        "saved_searches": saved_searches,
        "frequency": frequency,
    }

    # Render email templates
    txt_content = render_to_string("email/digest_update.txt", context=context)
    html_content = get_template("email/digest_update.html").render(context=context)

    msg = EmailMultiAlternatives(
        subject=f"Your {frequency.capitalize()} Civic Observer Digest",
        to=[user.email],
        from_email="Civic Observer <noreply@civic.observer>",
        body=txt_content,
    )
    msg.attach_alternative(html_content, "text/html")
    msg.esp_extra = {"MessageStream": "outbound"}  # type: ignore

    saved_search_ids = [saved_search.id for saved_search in saved_searches]

    # Clear pending first (single statement, committed independently of the send).
    SavedSearch.objects.filter(id__in=saved_search_ids).update(
        has_pending_results=False,
        last_notification_sent=timezone.now(),
    )

    try:
        msg.send()
    except Exception:
        logger.exception(
            "Failed to send %s digest to %s; re-flagging for retry",
            frequency,
            user.email,
        )
        SavedSearch.objects.filter(id__in=saved_search_ids).update(
            has_pending_results=True
        )
        return False

    return True


def _send_to_notification_channels(saved_search, new_pages) -> None:
    """
    Send notification to user's configured notification channels.

    Args:
        saved_search: The SavedSearch that matched
        new_pages: QuerySet of new MeetingPage objects
    """
    from notifications.services import dispatch_to_all_channels

    # Format message for non-email channels
    message = _format_channel_message(saved_search, new_pages)

    # Dispatch to all configured channels
    results = dispatch_to_all_channels(saved_search, message)

    for result in results:
        if result["success"]:
            logger.info(
                f"Sent {result['platform']} notification for SavedSearch {saved_search.id}"
            )
        else:
            logger.warning(
                f"Failed to send {result['platform']} notification for SavedSearch {saved_search.id}"
            )


def _format_channel_message(saved_search, new_pages) -> str:
    """Format notification message for non-email channels."""
    count = new_pages.count()
    search_name = saved_search.name

    if count == 1:
        page = new_pages.first()
        return (
            f'🔔 New result for "{search_name}"\n\n'
            f"Meeting: {page.document.meeting_name}\n"
            f"Date: {page.document.meeting_date}\n"
            f"Page {page.page_number}\n\n"
            f"View on Civic Observer: https://civic.observer/searches/"
        )
    else:
        return (
            f'🔔 {count} new results for "{search_name}"\n\n'
            f"View on Civic Observer: https://civic.observer/searches/"
        )
