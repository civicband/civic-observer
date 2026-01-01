# Resilient Meeting Data Backfill System

**Date:** 2025-12-31
**Status:** Approved
**Implementation Status:** ✅ Completed (2025-12-31)
**Implementation Plan:** See `docs/plans/2025-12-31-resilient-backfill.md`
**Context:** Large municipalities (547k+ pages) cause RQ job timeouts during backfill operations

## Problem Statement

The current backfill implementation processes all meeting data for a municipality in a single RQ job. For large municipalities with 100k+ pages (largest has 547k pages), this causes:

- Job timeouts (current limit: 10 minutes, but backfill can take 8-20 hours)
- No progress preservation on failure (must restart from beginning)
- Worker blocking for extended periods
- Poor visibility into backfill progress

## Solution Overview

Implement a **two-mode backfill system** that intelligently chooses between full and incremental backfill:

1. **Full Backfill Mode**: For new municipalities or forced full refresh
   - Batched job chaining (10 API pages per job)
   - Progress checkpointing in database
   - Resumable after failures
   - Takes hours but is fault-tolerant

2. **Incremental Backfill Mode**: For daily webhook updates
   - Single job fetching ±6 months of meetings
   - Uses date range filters
   - Fast (1-10 minutes even for large municipalities)
   - Default mode for existing municipalities

## Architecture

### System Components

**1. BackfillProgress Model**
- Tracks backfill state per municipality/document type
- Stores pagination cursor for resumability
- Configuration point for `force_full_backfill` flag
- Records status, mode, timestamps, and errors

**2. Smart Backfill Orchestrator**
- Entry point task called by webhook
- Examines municipality and determines mode:
  - New municipality → Full backfill
  - `force_full_backfill=True` → Full backfill
  - Existing municipality → Incremental backfill
- Routes to appropriate worker task

**3. Batched Worker Task**
- Processes one batch (10 API pages = ~10k records)
- Updates checkpoint after each batch
- Enqueues next batch if more work remains
- Used for full backfills

**4. Incremental Worker Task**
- Fetches only recent/upcoming meetings (±6 months)
- Uses date range filters on API
- Single job for most daily updates

### Workflow

```
Webhook arrives
    ↓
Orchestrator determines mode
    ↓
┌─────────────────┬─────────────────┐
│   Full Mode     │ Incremental Mode│
├─────────────────┼─────────────────┤
│ Batch 1 (10 pg) │  Single job     │
│ Save checkpoint │  (±6 months)    │
│ Chain to Batch 2│  Complete       │
│ Save checkpoint │                 │
│ Chain to Batch 3│                 │
│      ...        │                 │
│ Complete        │                 │
└─────────────────┴─────────────────┘
```

## Database Schema

### New Model: BackfillProgress

```python
class BackfillProgress(models.Model):
    municipality = models.ForeignKey(Muni, on_delete=models.CASCADE)
    document_type = models.CharField(
        max_length=20, choices=[("agenda", "Agenda"), ("minutes", "Minutes")]
    )
    mode = models.CharField(
        max_length=20, choices=[("full", "Full"), ("incremental", "Incremental")]
    )
    status = models.CharField(
        max_length=20,
        choices=[
            ("pending", "Pending"),
            ("in_progress", "In Progress"),
            ("completed", "Completed"),
            ("failed", "Failed"),
        ],
    )
    next_cursor = models.TextField(blank=True, null=True)  # Pagination cursor
    force_full_backfill = models.BooleanField(default=False)  # Admin flag
    started_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    error_message = models.TextField(blank=True, null=True)

    class Meta:
        unique_together = [["municipality", "document_type"]]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["municipality", "status"]),
        ]
```

### Configuration Constants

```python
INCREMENTAL_BACKFILL_MONTHS = 6  # ±6 months from today
FULL_BACKFILL_BATCH_SIZE = 10  # API pages per job
API_PAGE_SIZE = 1000  # Records per API request
```

## Implementation Details

### 1. Orchestrator Task

```python
def backfill_municipality_meetings_task(muni_id: UUID | str) -> dict[str, str]:
    """Main orchestrator that routes to full or incremental backfill."""
    muni = Muni.objects.get(pk=muni_id)

    # Process agendas
    progress_agendas, _ = BackfillProgress.objects.get_or_create(
        municipality=muni, document_type="agenda"
    )

    # Determine mode
    is_new = not MeetingDocument.objects.filter(
        municipality=muni, document_type="agenda"
    ).exists()
    needs_full = is_new or progress_agendas.force_full_backfill

    if needs_full:
        # Start full backfill chain
        progress_agendas.mode = "full"
        progress_agendas.status = "in_progress"
        progress_agendas.next_cursor = None  # Start from beginning
        progress_agendas.save()

        queue.enqueue(backfill_batch_task, muni_id, "agenda", progress_agendas.id)
    else:
        # Run incremental backfill
        progress_agendas.mode = "incremental"
        progress_agendas.status = "in_progress"
        progress_agendas.save()

        queue.enqueue(backfill_incremental_task, muni_id, "agenda", progress_agendas.id)

    # Repeat for minutes...
```

### 2. Full Backfill: Batched Job with Chaining

```python
def backfill_batch_task(muni_id: UUID, document_type: str, progress_id: int):
    """Process one batch (10 API pages), save checkpoint, chain next."""
    muni = Muni.objects.get(pk=muni_id)
    progress = BackfillProgress.objects.get(pk=progress_id)

    try:
        # Fetch and process up to 10 API pages
        stats, next_cursor = _fetch_batch(
            muni, document_type, start_cursor=progress.next_cursor, max_pages=10
        )

        # Update checkpoint
        progress.next_cursor = next_cursor
        progress.error_message = None
        progress.updated_at = timezone.now()

        if next_cursor:
            # More work to do - enqueue next batch
            progress.save()
            queue.enqueue(backfill_batch_task, muni_id, document_type, progress_id)
        else:
            # Done with this document type
            progress.status = "completed"
            if progress.force_full_backfill:
                # Clear the flag after successful full backfill
                progress.force_full_backfill = False
            progress.save()

        return stats

    except Exception as e:
        # Save failure state
        progress.status = "failed"
        progress.error_message = str(e)
        progress.save()
        logger.error(f"Backfill batch failed: {e}", exc_info=True)
        raise  # Let RQ mark the job as failed
```

### 3. Incremental Backfill: Single Job with Date Filter

```python
def backfill_incremental_task(muni_id: UUID, document_type: str, progress_id: int):
    """Fetch only meetings within ±6 months of today."""
    muni = Muni.objects.get(pk=muni_id)
    progress = BackfillProgress.objects.get(pk=progress_id)

    try:
        # Calculate date range
        today = date.today()
        start_date = today - timedelta(days=180)  # 6 months ago
        end_date = today + timedelta(days=180)  # 6 months from now

        # Fetch with date filters
        stats = _fetch_date_range(
            muni, document_type, start_date=start_date, end_date=end_date
        )

        progress.status = "completed"
        progress.save()

        return stats

    except Exception as e:
        progress.status = "failed"
        progress.error_message = str(e)
        progress.save()
        logger.error(f"Incremental backfill failed: {e}", exc_info=True)
        raise
```

### 4. API Fetch with Date Filtering

```python
def _backfill_document_type(
    muni: Muni,
    table_name: str,
    document_type: str,
    start_cursor: str | None = None,
    max_pages: int | None = None,  # NEW: limit for batching
    date_range: tuple[date, date] | None = None,  # NEW: for incremental
    timeout: int = 60,
) -> tuple[dict[str, int], str | None]:
    """
    Backfill with optional pagination cursor, batch limit, and date filtering.

    Returns: (stats, next_cursor)
    """
    base_url = f"https://{muni.subdomain}.civic.band/meetings/{table_name}.json"

    # Build query parameters
    params = {"_size": 1000}

    # Add date filtering for incremental mode
    if date_range:
        start_date, end_date = date_range
        params["date__gte"] = start_date.isoformat()
        params["date__lte"] = end_date.isoformat()

    # Resume from cursor if provided
    if start_cursor:
        params["_next"] = start_cursor

    pages_fetched = 0
    next_cursor = None
    stats = {
        "documents_created": 0,
        "documents_updated": 0,
        "pages_created": 0,
        "pages_updated": 0,
        "errors": 0,
    }

    with httpx.Client(timeout=timeout, headers=headers) as client:
        while True:
            response = client.get(base_url, params=params)
            response.raise_for_status()
            data = response.json()

            _process_rows_batch(muni, data.get("rows", []), document_type, stats)
            pages_fetched += 1

            # Check for next page
            next_cursor = data.get("next")

            # Stop if: no more pages OR reached batch limit
            if not next_cursor or (max_pages and pages_fetched >= max_pages):
                break

            params["_next"] = next_cursor

    return stats, next_cursor
```

## Error Handling & Recovery

### Failure Scenarios

**1. Mid-batch API failure:**
- Current batch job fails and is marked failed in RQ
- `BackfillProgress.status` set to 'failed' with error message
- `next_cursor` preserved at last successful checkpoint
- Next webhook trigger resumes from checkpoint

**2. Worker crash:**
- Job remains in-progress in RQ (eventually times out)
- `BackfillProgress` retains last checkpoint
- Next webhook trigger detects incomplete backfill and resumes

**3. Network timeout:**
- HTTP client timeout (60s) raises exception
- Handled same as API failure above

### Resume Logic

When webhook arrives for municipality with existing BackfillProgress:

```python
progress = BackfillProgress.objects.get(municipality=muni, document_type="agenda")

if progress.status == "failed":
    # Resume from last checkpoint
    progress.status = "in_progress"
    progress.save()
    queue.enqueue(backfill_batch_task, muni_id, "agenda", progress.id)
elif progress.status == "in_progress":
    # Job still running or crashed - let it continue
    # (Could add logic to detect stale jobs and restart)
    pass
```

## Admin Interface

Add `BackfillProgress` to Django admin:

**List Display:**
- municipality (with link to Municipality admin)
- document_type
- mode
- status (with color coding)
- updated_at
- progress indicator (estimated % based on typical page counts)

**Filters:**
- status
- mode
- document_type

**Actions:**
- "Force full backfill" - sets flag and triggers new backfill job
- "Retry failed backfill" - resets status to in_progress and re-enqueues

**Detail View:**
- Show error_message for failed jobs
- Show next_cursor for debugging
- Display job chain history (if we track job IDs)

## Performance Characteristics

### Full Backfill (547k pages)

- API requests: ~547 (at 1000 per page)
- RQ jobs: ~55 (at 10 pages per job)
- Time per job: 5-10 minutes
- Total time: 4-10 hours (for agendas), similar for minutes
- Worker blocking per job: 5-10 minutes (acceptable)
- Progress checkpoints: Every 10k records

### Incremental Backfill (±6 months)

- Typical page count: ~50-500 pages (varies by municipality)
- RQ jobs: 1
- Time per job: 1-10 minutes
- Worker blocking: Minimal
- Frequency: Once per day per large municipality

### Database Impact

- New writes per full backfill: ~55 checkpoint updates
- New writes per incremental: 2 (start + complete)
- Storage: Minimal (cursors are small strings)
- Queries: Simple get/update on indexed fields

## Testing Strategy

### Unit Tests

```python
# Decision logic
test_new_municipality_triggers_full_backfill()
test_existing_municipality_uses_incremental()
test_force_flag_triggers_full_backfill()

# Batch processing
test_batch_task_saves_checkpoint()
test_batch_task_chains_when_more_pages()
test_batch_task_completes_when_done()

# Incremental mode
test_incremental_uses_date_filters()
test_incremental_fetches_six_months()

# Error handling
test_failed_batch_marks_progress_as_failed()
test_resume_from_checkpoint_after_failure()
```

### Integration Tests

```python
test_full_backfill_with_mocked_api()  # Mock 3 pages, verify all fetched
test_incremental_only_fetches_recent_meetings()  # Verify date filters work
```

### Manual Testing Checklist

- [ ] New municipality (0 pages) → full backfill works
- [ ] Existing municipality → incremental works
- [ ] Set force_full_backfill flag → triggers full backfill
- [ ] Kill worker mid-batch → resumes from checkpoint on next webhook
- [ ] Monitor RQ dashboard during large backfill → see job chain
- [ ] Check BackfillProgress admin → see status and cursors
- [ ] Verify date filters in API requests (±6 months)

## Migration Strategy

### Phase 1: Add Model & Basic Infrastructure
- Create `BackfillProgress` model and migration
- Add to Django admin
- Deploy (no behavior change yet)

### Phase 2: Implement New Tasks
- Create new task functions (orchestrator, batch, incremental)
- Add configuration constants
- Update `_backfill_document_type` with new parameters
- Test in development

### Phase 3: Gradual Rollout
- Update webhook to use new orchestrator
- Monitor first few backfills closely
- Watch for errors in RQ dashboard and BackfillProgress admin

### Phase 4: Cleanup
- Remove old monolithic backfill logic (if fully replaced)
- Update documentation

## Open Questions / Future Enhancements

1. **Concurrent workers**: If we scale to multiple workers, could parallelize batch jobs
2. **Smart batch sizing**: Could adjust batch size based on municipality size
3. **Progress reporting**: Could expose backfill progress to users/admins
4. **Retry policy**: Could implement exponential backoff for failed jobs
5. **Stale job detection**: Could detect jobs stuck in-progress and auto-retry

## References

- Current implementation: `meetings/tasks.py`, `meetings/services.py`
- Webhook endpoint: `municipalities/views.py:MuniWebhookUpdateView`
- RQ configuration: `config/settings/base.py` (10-minute timeout)
