"""
Background tasks for meeting data backfill operations.

This module implements a two-mode backfill system:

1. Full Backfill Mode:
   - Used for new municipalities or when force_full_backfill flag is set
   - Processes all historical data in batches (10 API pages per job)
   - Jobs chain together, saving progress checkpoints after each batch
   - Resumable after failures

2. Incremental Backfill Mode:
   - Used for daily webhook updates on existing municipalities
   - Fetches only ±6 months of meeting data using date filters
   - Single job, completes quickly

The orchestrator (backfill_municipality_meetings_task) determines which mode
to use based on municipality state and configuration.
"""

import logging
from uuid import UUID

import django_rq

from municipalities.models import Muni

logger = logging.getLogger(__name__)


def _enqueue_saved_search_checks(municipality_id) -> None:
    """Kick off saved-search notification/digest checks for one municipality."""
    from searches.tasks import check_saved_searches

    try:
        queue = django_rq.get_queue("default")
        queue.enqueue(check_saved_searches, municipality_id)
    except Exception as e:
        logger.error(f"Failed to enqueue saved search checks: {e}", exc_info=True)


def _mark_municipality_indexed(muni) -> None:
    """Record that meeting data was successfully indexed for a municipality."""
    from django.utils import timezone

    muni.last_indexed = timezone.now()
    muni.save(update_fields=["last_indexed"])


def _enqueue_backfill(queue, progress, task, *args):
    """Enqueue a backfill job with an explicit timeout.

    If enqueueing fails (e.g. Redis is unavailable), the progress record is
    marked failed so a later webhook can retry immediately instead of waiting
    for the stale-job recovery window.
    """
    from django.conf import settings

    job_timeout = getattr(settings, "BACKFILL_JOB_TIMEOUT", 900)
    try:
        return queue.enqueue(task, *args, job_timeout=job_timeout)
    except Exception as e:
        progress.status = "failed"
        progress.error_message = f"Failed to enqueue backfill job: {e}"
        progress.save()
        raise


def backfill_municipality_meetings_task(muni_id: UUID | str) -> dict[str, str]:
    """
    Main orchestrator task that routes to full or incremental backfill.

    Determines backfill mode based on:
    - New municipality (no existing documents) → Full backfill
    - force_full_backfill flag set → Full backfill
    - Existing municipality → Incremental backfill (±6 months)

    Prevents concurrent backfills by checking for active jobs.

    Args:
        muni_id: Primary key of the Municipality to backfill

    Returns:
        Dictionary with status for agendas and minutes

    Raises:
        Muni.DoesNotExist: If the municipality doesn't exist
    """
    from datetime import timedelta

    from django.db import transaction
    from django.utils import timezone

    from meetings.models import BackfillJob, BackfillProgress, MeetingDocument

    logger.info(f"Starting backfill orchestrator for municipality ID: {muni_id}")

    try:
        muni = Muni.objects.get(pk=muni_id)
        queue = django_rq.get_queue("default")
        result = {}

        # Process both agendas and minutes
        for document_type in ["agenda", "minutes"]:
            # Use atomic transaction with select_for_update to prevent race conditions
            with transaction.atomic():
                # Get or create progress tracker with lock
                progress, created = (
                    BackfillProgress.objects.select_for_update().get_or_create(
                        municipality=muni,
                        document_type=document_type,
                    )
                )

                # Check if already running
                if progress.status == "in_progress":
                    # Check if job is stale (no update in last hour)
                    stale_threshold = timezone.now() - timedelta(hours=1)
                    if progress.updated_at < stale_threshold:
                        logger.warning(
                            f"Detected stale BackfillProgress for {muni.subdomain} "
                            f"{document_type} (last update: {progress.updated_at}), "
                            f"marking as failed and restarting"
                        )
                        progress.status = "failed"
                        progress.error_message = "Job timed out (no update in 1+ hour)"
                        progress.save()
                        # Continue to start new job
                    else:
                        logger.info(
                            f"BackfillProgress already in progress for {muni.subdomain} "
                            f"{document_type} (started: {timezone.now() - progress.updated_at} ago)"
                        )
                        result[document_type] = "already_running:BackfillProgress"
                        continue

                # Also check for active BackfillJob (from management command)
                active_job = BackfillJob.objects.filter(
                    municipality=muni,
                    document_type=document_type,
                    status__in=["pending", "running"],
                ).first()

                if active_job:
                    logger.info(
                        f"BackfillJob already active for {muni.subdomain} {document_type} "
                        f"(ID: {active_job.id}, status: {active_job.status})"
                    )
                    result[document_type] = (
                        f"already_running:BackfillJob:{active_job.id}"
                    )
                    continue

                # Determine if full backfill is needed
                is_new = not MeetingDocument.objects.filter(
                    municipality=muni,
                    document_type=document_type,
                ).exists()
                needs_full = is_new or progress.force_full_backfill

                # Transition to in_progress *inside* the lock. Doing it after
                # the transaction released the row lock let two concurrent
                # orchestrators both pass the guard and enqueue duplicates.
                progress.mode = "full" if needs_full else "incremental"
                progress.status = "in_progress"
                if needs_full:
                    progress.next_cursor = None  # Start from beginning
                progress.error_message = None
                progress.save()

            # Enqueue outside the lock; the committed in_progress status now
            # blocks a concurrent orchestrator from enqueueing a duplicate.
            if needs_full:
                job = _enqueue_backfill(
                    queue,
                    progress,
                    backfill_batch_task,
                    muni_id,
                    document_type,
                    progress.id,
                )
                reason = "new municipality" if is_new else "force_full_backfill flag"
                logger.info(
                    f"Enqueued full backfill for {muni.subdomain} {document_type} "
                    f"({reason}, job ID: {job.id})"
                )
                result[document_type] = f"full_backfill_started:{job.id}"
            else:
                job = _enqueue_backfill(
                    queue,
                    progress,
                    backfill_incremental_task,
                    muni_id,
                    document_type,
                    progress.id,
                )
                logger.info(
                    f"Enqueued incremental backfill for {muni.subdomain} {document_type} "
                    f"(job ID: {job.id})"
                )
                result[document_type] = f"incremental_backfill_started:{job.id}"

        # Immediate saved-search notifications are enqueued by the completion
        # handlers of the batch/incremental backfill tasks.

        return result

    except Muni.DoesNotExist:
        logger.error(f"Municipality with ID {muni_id} does not exist")
        raise
    except Exception as e:
        logger.error(
            f"Backfill orchestrator failed for municipality ID {muni_id}: {e}",
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

        # Invalidate search cache for this municipality
        from searches.cache import invalidate_search_cache_for_municipality

        invalidate_search_cache_for_municipality(int(muni.id))

        _enqueue_saved_search_checks(int(muni.id))

        _mark_municipality_indexed(muni)

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
            job = _enqueue_backfill(
                queue,
                progress,
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

            # Invalidate search cache for this municipality
            from searches.cache import invalidate_search_cache_for_municipality

            invalidate_search_cache_for_municipality(int(muni.id))

            _enqueue_saved_search_checks(int(muni.id))

            _mark_municipality_indexed(muni)

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
