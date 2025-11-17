"""
Background tasks for meeting data backfill operations.
"""

import logging
from uuid import UUID

from municipalities.models import Muni

from .services import backfill_municipality_meetings

logger = logging.getLogger(__name__)


def backfill_municipality_meetings_task(muni_id: UUID | str) -> dict[str, int]:
    """
    Background task to backfill all meeting data for a municipality.

    This task is designed to be run asynchronously via django-rq to avoid
    blocking the web request when triggered from webhooks or admin actions.

    Args:
        muni_id: Primary key of the Municipality to backfill

    Returns:
        Dictionary with statistics from the backfill operation

    Raises:
        Muni.DoesNotExist: If the municipality doesn't exist
    """
    logger.info(f"Starting background backfill task for municipality ID: {muni_id}")

    try:
        muni = Muni.objects.get(pk=muni_id)
        stats = backfill_municipality_meetings(muni)
        logger.info(f"Background backfill completed for {muni.subdomain}: {stats}")
        return stats
    except Muni.DoesNotExist:
        logger.error(f"Municipality with ID {muni_id} does not exist")
        raise
    except Exception as e:
        logger.error(
            f"Background backfill failed for municipality ID {muni_id}: {e}",
            exc_info=True,
        )
        raise
