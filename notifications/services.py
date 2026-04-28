"""
Background tasks for saved search notifications.

These tasks handle checking saved searches and sending notifications when
new matching pages are found.
"""

import logging
from collections import defaultdict

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.template.loader import get_template, render_to_string
from django.utils import timezone

from .senders import get_sender

logger = logging.getLogger(__name__)


def dispatch_notification(channel, message: str) -> bool:
    """
    Dispatch a notification to a single channel.

    Args:
        channel: The notification channel to send to
        message: The message content

    Returns:
        True if successful, False otherwise
    """
    if not channel.is_enabled:
        logger.debug(
            f"Skipping disabled channel {channel.platform} for {channel.user.email}"
        )
        return False

    sender = get_sender(channel.platform)
    if not sender:
        logger.error(f"No sender found for platform: {channel.platform}")
        return False

    try:
        success = sender.send(channel, message)

        if success:
            channel.record_success()
            return True
        else:
            channel.record_failure()
            return False

    except Exception as e:
        logger.exception(f"Error dispatching to {channel.platform}: {e}")
        channel.record_failure()
        return False


def dispatch_to_all_channels(
    saved_search,
    message: str,
) -> list[dict]:
    """
    Dispatch notification to all effective channels for a saved search.

    Args:
        saved_search: The saved search triggering the notification
        message: The message content

    Returns:
        List of result dicts with platform, success, and error keys
    """

    channels = saved_search.get_effective_channels()
    results = []

    for channel in channels:
        success = dispatch_notification(channel, message)
        results.append(
            {
                "platform": channel.platform,
                "success": success,
                "channel_id": str(channel.id),
            }
        )

    return results


def send_meeting_digest_email(user, meetings, meeting_date) -> None:
    """Send a consolidated daily digest email with today's meetings.

    Args:
        user: The user to send the digest to.
        meetings: QuerySet of MeetingDocument objects for today.
        meeting_date: The date of the meetings being reported.

    Note:
        The caller (management command) is responsible for updating
        last_digest_sent after successful delivery.
    """
    grouped_by_muni = defaultdict(list)
    for meeting in meetings:
        grouped_by_muni[meeting.municipality].append(meeting)

    context = {
        "user": user,
        "grouped_meetings": list(grouped_by_muni.items()),
        "meeting_date": meeting_date,
    }

    txt_content = render_to_string("email/meeting_digest.txt", context=context)
    html_content = get_template("email/meeting_digest.html").render(context=context)

    msg = EmailMultiAlternatives(
        subject=f"Today's Civic Meetings — {meeting_date}",
        to=[user.email],
        from_email=settings.DEFAULT_FROM_EMAIL,
        body=txt_content,
    )
    msg.attach_alternative(html_content, "text/html")
    msg.esp_extra = {"MessageStream": "outbound"}  # type: ignore[attr-defined]
    msg.send()


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
    from searches.models import SavedSearch

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
    from searches.models import SavedSearch

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
        "pending_marked": 0,
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
    from searches.models import SavedSearch

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
    from searches.models import SavedSearch

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
    msg.esp_extra = {"MessageStream": "outbound"}  # type: ignore[attr-defined]

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

        SavedSearch = saved_searches[0].__class__
        SavedSearch.objects.bulk_update(
            saved_searches,
            ["has_pending_results", "last_notification_sent"],
        )


def _send_to_notification_channels(saved_search, new_pages) -> None:
    """
    Send notification to user's configured notification channels.

    Args:
        saved_search: The SavedSearch that matched
        new_pages: QuerySet of new MeetingPage objects
    """
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
