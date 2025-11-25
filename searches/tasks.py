"""
Background tasks for saved search notifications.

These tasks handle checking saved searches and sending notifications when
new matching pages are found.
"""

import logging

from django.db import transaction
from django.utils import timezone

from .models import SavedSearch

logger = logging.getLogger(__name__)


def check_saved_search_for_updates(saved_search_id) -> dict[str, str | int]:
    """
    Check a single saved search for new results and send notification if needed.

    Args:
        saved_search_id: ID of the SavedSearch to check

    Returns:
        Dict with status information:
        - status: "not_found" | "no_new_results" | "notified" | "pending"
        - saved_search_id: The ID that was checked
        - new_results_count: Number of new results (if applicable)
        - action: Description of action taken

    This function:
    1. Gets new pages matching the search (created since last_checked_for_new_pages)
    2. If notification_frequency is "immediate" and there are new results, sends email
    3. If notification_frequency is "daily" or "weekly", flags has_pending_results
    4. Updates the Search object's tracking fields
    """
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

    # Get new pages for this search
    new_pages = saved_search.search.update_search()

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
        # Send notification immediately
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
        saved_search.last_checked = timezone.now()
        saved_search.save(update_fields=["has_pending_results", "last_checked"])
        logger.info(
            f"Flagged SavedSearch {saved_search.id} for {saved_search.notification_frequency} digest"
        )
        return {
            "status": "pending",
            "saved_search_id": str(saved_search.id),
            "new_results_count": new_results_count,
            "action": f"Marked for {saved_search.notification_frequency} digest",
        }


def check_all_immediate_searches() -> dict[str, int]:
    """
    Check all saved searches with immediate notification frequency.

    This should be called after new pages are ingested (e.g., from webhook or backfill).
    It checks each saved search and sends notifications for any new matches.

    Returns:
        Dict with statistics:
        - searches_checked: Total number of immediate searches checked
        - emails_sent: Number of notification emails sent
        - pending_marked: Number marked as pending (should be 0 for immediate)
        - errors: Number of errors encountered
    """
    immediate_searches = SavedSearch.objects.filter(
        notification_frequency="immediate"
    ).select_related("search", "user")

    total_count = immediate_searches.count()
    logger.info(f"Checking {total_count} saved searches with immediate notification")

    emails_sent = 0
    errors = 0

    for saved_search in immediate_searches:
        result = check_saved_search_for_updates(saved_search.id)
        if result["status"] == "notified":
            emails_sent += 1
        elif result["status"] == "not_found":
            errors += 1

    logger.info(f"Checked {total_count} immediate searches: {emails_sent} emails sent")

    return {
        "searches_checked": total_count,
        "emails_sent": emails_sent,
        "pending_marked": 0,  # Immediate searches don't mark pending
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
        _send_digest_email(user, user_searches, frequency="daily")
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
        _send_digest_email(user, user_searches, frequency="weekly")
        emails_sent += 1
        logger.info(
            f"Sent weekly digest to {user.email} with {len(user_searches)} saved searches"
        )

    logger.info(
        f"Weekly digest complete: {emails_sent} emails sent for {total_searches} searches"
    )

    return {"emails_sent": emails_sent, "searches_notified": total_searches}


def _send_digest_email(user, saved_searches, frequency="daily"):
    """
    Helper function to send a digest email for multiple saved searches.

    Args:
        user: User to send email to
        saved_searches: List of SavedSearch objects with pending results
        frequency: "daily" or "weekly"

    Raises:
        EmailError: If email sending fails
        DatabaseError: If database update fails

    Uses atomic transaction to ensure email send and database updates happen together.
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

    # Send email
    msg = EmailMultiAlternatives(
        subject=f"Your {frequency.capitalize()} Civic Observer Digest",
        to=[user.email],
        from_email="Civic Observer <noreply@civic.observer>",
        body=txt_content,
    )
    msg.attach_alternative(html_content, "text/html")
    msg.esp_extra = {"MessageStream": "outbound"}  # type: ignore

    # Use transaction to ensure email and DB updates are atomic
    with transaction.atomic():
        # Send email first - if this fails, transaction rolls back
        msg.send()

        # Update all saved searches: clear pending flag and update last_notification_sent
        # Use bulk_update for better performance
        now = timezone.now()
        for saved_search in saved_searches:
            saved_search.has_pending_results = False
            saved_search.last_notification_sent = now

        SavedSearch.objects.bulk_update(
            saved_searches,
            ["has_pending_results", "last_notification_sent"],
        )
