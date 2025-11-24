"""
Background tasks for saved search notifications.

These tasks handle checking saved searches and sending notifications when
new matching pages are found.
"""

import logging

from django.utils import timezone

from .models import SavedSearch

logger = logging.getLogger(__name__)


def check_saved_search_for_updates(saved_search_id):
    """
    Check a single saved search for new results and send notification if needed.

    Args:
        saved_search_id: ID of the SavedSearch to check

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
        return

    # Get new pages for this search
    new_pages = saved_search.search.update_search()

    # If no new results, nothing to do
    if not new_pages.exists():
        logger.debug(
            f"No new results for SavedSearch {saved_search.id} ({saved_search.name})"
        )
        return

    logger.info(
        f"Found {new_pages.count()} new results for SavedSearch {saved_search.id} ({saved_search.name})"
    )

    # Handle based on notification frequency
    if saved_search.notification_frequency == "immediate":
        # Send notification immediately
        saved_search.send_search_notification(new_pages=new_pages)
        logger.info(
            f"Sent immediate notification for SavedSearch {saved_search.id} to {saved_search.user.email}"
        )
    else:
        # Flag for digest notification
        saved_search.has_pending_results = True
        saved_search.last_checked = timezone.now()
        saved_search.save(update_fields=["has_pending_results", "last_checked"])
        logger.info(
            f"Flagged SavedSearch {saved_search.id} for {saved_search.notification_frequency} digest"
        )


def check_all_immediate_searches():
    """
    Check all saved searches with immediate notification frequency.

    This should be called after new pages are ingested (e.g., from webhook or backfill).
    It checks each saved search and sends notifications for any new matches.
    """
    immediate_searches = SavedSearch.objects.filter(
        notification_frequency="immediate"
    ).select_related("search", "user")

    logger.info(
        f"Checking {immediate_searches.count()} saved searches with immediate notification"
    )

    for saved_search in immediate_searches:
        check_saved_search_for_updates(saved_search.id)


def send_daily_digests():
    """
    Send daily digest emails to users with pending results.

    This should be scheduled to run once per day (e.g., via cron or django-rq-scheduler).
    It groups saved searches by user and sends one email per user containing all their
    pending daily digest searches.
    """
    from collections import defaultdict

    # Get all daily saved searches with pending results
    daily_searches = SavedSearch.objects.filter(
        notification_frequency="daily", has_pending_results=True
    ).select_related("search", "user")

    logger.info(f"Sending daily digests for {daily_searches.count()} saved searches")

    # Group by user
    searches_by_user = defaultdict(list)
    for saved_search in daily_searches:
        searches_by_user[saved_search.user].append(saved_search)

    # Send one email per user
    for user, user_searches in searches_by_user.items():
        _send_digest_email(user, user_searches, frequency="daily")
        logger.info(
            f"Sent daily digest to {user.email} with {len(user_searches)} saved searches"
        )


def send_weekly_digests():
    """
    Send weekly digest emails to users with pending results.

    This should be scheduled to run once per week (e.g., via cron or django-rq-scheduler).
    It groups saved searches by user and sends one email per user containing all their
    pending weekly digest searches.
    """
    from collections import defaultdict

    # Get all weekly saved searches with pending results
    weekly_searches = SavedSearch.objects.filter(
        notification_frequency="weekly", has_pending_results=True
    ).select_related("search", "user")

    logger.info(f"Sending weekly digests for {weekly_searches.count()} saved searches")

    # Group by user
    searches_by_user = defaultdict(list)
    for saved_search in weekly_searches:
        searches_by_user[saved_search.user].append(saved_search)

    # Send one email per user
    for user, user_searches in searches_by_user.items():
        _send_digest_email(user, user_searches, frequency="weekly")
        logger.info(
            f"Sent weekly digest to {user.email} with {len(user_searches)} saved searches"
        )


def _send_digest_email(user, saved_searches, frequency="daily"):
    """
    Helper function to send a digest email for multiple saved searches.

    Args:
        user: User to send email to
        saved_searches: List of SavedSearch objects with pending results
        frequency: "daily" or "weekly"
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

    msg.send()

    # Update all saved searches: clear pending flag and update last_notification_sent
    for saved_search in saved_searches:
        saved_search.has_pending_results = False
        saved_search.last_notification_sent = timezone.now()
        saved_search.save(
            update_fields=["has_pending_results", "last_notification_sent"]
        )
