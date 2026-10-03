"""
Notification dispatch services.

Provides channel dispatch (Discord, Slack, Bluesky, Mastodon) and the
consolidated daily meeting digest email. Saved-search notification and
digest orchestration lives in `searches.tasks`.
"""

import logging
from collections import defaultdict

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import get_template, render_to_string

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
