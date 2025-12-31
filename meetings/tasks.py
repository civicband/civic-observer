"""
Background tasks for meeting data backfill operations.
"""

import logging
from uuid import UUID

import django_rq

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

        # Enqueue check for immediate searches as a background task
        try:
            import django_rq

            from searches.tasks import check_all_immediate_searches

            queue = django_rq.get_queue("default")
            job = queue.enqueue(check_all_immediate_searches)
            logger.info(
                f"Enqueued check for immediate searches (job ID: {job.id}) after backfill"
            )
        except Exception as e:
            # Don't fail the backfill if search checking enqueue fails
            logger.error(
                f"Failed to enqueue immediate search check: {e}", exc_info=True
            )

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


def backfill_incremental_task(
    muni_id: UUID | str, document_type: str, progress_id: int
) -> dict[str, int]:
    """
    Background task for incremental backfill (±6 months).

    Fetches only meetings within INCREMENTAL_BACKFILL_MONTHS of today
    using date range filters. Designed for daily webhook updates.

    Args:
        muni_id: Municipality primary key
        document_type: 'agenda' or 'minutes'
        progress_id: BackfillProgress record ID

    Returns:
        Statistics dictionary from backfill operation

    Raises:
        Exception: If backfill fails (after updating progress status)
    """
    from datetime import date, timedelta

    from django.conf import settings

    from meetings.models import BackfillProgress
    from meetings.services import _backfill_document_type

    logger.info(
        f"Starting incremental backfill task for municipality ID: {muni_id}, "
        f"document_type: {document_type}"
    )

    try:
        muni = Muni.objects.get(pk=muni_id)
        progress = BackfillProgress.objects.get(pk=progress_id)

        # Calculate date range (±6 months from today)
        today = date.today()
        months = getattr(settings, "INCREMENTAL_BACKFILL_MONTHS", 6)
        start_date = today - timedelta(days=months * 30)
        end_date = today + timedelta(days=months * 30)

        logger.info(
            f"Incremental backfill for {muni.subdomain} {document_type}: "
            f"{start_date} to {end_date}"
        )

        # Fetch with date filters
        table_name = "agendas" if document_type == "agenda" else "minutes"
        stats, _ = _backfill_document_type(
            muni=muni,
            table_name=table_name,
            document_type=document_type,
            date_range=(start_date, end_date),
        )

        # Mark progress as completed
        progress.status = "completed"
        progress.error_message = None
        progress.save()

        logger.info(
            f"Incremental backfill completed for {muni.subdomain} {document_type}: {stats}"
        )

        return stats

    except Muni.DoesNotExist:
        logger.error(f"Municipality with ID {muni_id} does not exist")
        raise
    except Exception as e:
        # Save failure state
        try:
            progress = BackfillProgress.objects.get(pk=progress_id)
            progress.status = "failed"
            progress.error_message = str(e)
            progress.save()
        except Exception as save_error:
            logger.error(f"Failed to update progress status: {save_error}")

        logger.error(
            f"Incremental backfill failed for municipality ID {muni_id}: {e}",
            exc_info=True,
        )
        raise


def backfill_batch_task(
    muni_id: UUID | str, document_type: str, progress_id: int
) -> dict[str, int]:
    """
    Background task for batched full backfill.

    Processes one batch (FULL_BACKFILL_BATCH_SIZE API pages), saves
    checkpoint, and enqueues next batch if more work remains.

    Args:
        muni_id: Municipality primary key
        document_type: 'agenda' or 'minutes'
        progress_id: BackfillProgress record ID

    Returns:
        Statistics dictionary from this batch

    Raises:
        Exception: If batch fails (after updating progress status)
    """
    from django.conf import settings

    from meetings.models import BackfillProgress
    from meetings.services import _backfill_document_type

    logger.info(
        f"Starting batch backfill task for municipality ID: {muni_id}, "
        f"document_type: {document_type}, progress_id: {progress_id}"
    )

    try:
        muni = Muni.objects.get(pk=muni_id)
        progress = BackfillProgress.objects.get(pk=progress_id)

        # Ensure status is correct, especially for retries
        if progress.status != "in_progress":
            progress.status = "in_progress"
            progress.save()

        # Get batch size from settings
        batch_size = getattr(settings, "FULL_BACKFILL_BATCH_SIZE", 10)

        logger.info(
            f"Processing batch for {muni.subdomain} {document_type}, "
            f"starting from cursor: {progress.next_cursor}"
        )

        # Fetch and process one batch
        table_name = "agendas" if document_type == "agenda" else "minutes"
        stats, next_cursor = _backfill_document_type(
            muni=muni,
            table_name=table_name,
            document_type=document_type,
            start_cursor=progress.next_cursor,
            max_pages=batch_size,
        )

        # Update checkpoint
        progress.next_cursor = next_cursor
        progress.error_message = None

        if next_cursor:
            # More work to do - save and enqueue next batch
            progress.save()

            queue = django_rq.get_queue("default")
            job = queue.enqueue(
                backfill_batch_task,
                muni_id,
                document_type,
                progress_id,
            )
            logger.info(
                f"Enqueued next batch for {muni.subdomain} {document_type} "
                f"(job ID: {job.id})"
            )
        else:
            # Done - mark complete and clear flag
            progress.status = "completed"
            if progress.force_full_backfill:
                progress.force_full_backfill = False
            progress.save()

            logger.info(
                f"Batch backfill completed for {muni.subdomain} {document_type}"
            )

        return stats

    except Muni.DoesNotExist:
        logger.error(f"Municipality with ID {muni_id} does not exist")
        raise
    except Exception as e:
        # Save failure state
        try:
            progress = BackfillProgress.objects.get(pk=progress_id)
            progress.status = "failed"
            progress.error_message = str(e)
            progress.save()
        except Exception as save_error:
            logger.error(f"Failed to update progress status: {save_error}")

        logger.error(
            f"Batch backfill failed for municipality ID {muni_id}: {e}",
            exc_info=True,
        )
        raise
