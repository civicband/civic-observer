# Resilient Backfill System

The resilient backfill system provides reliable backfilling of meeting data from civic.band with automatic checkpoint/resume capability and verification.

## Architecture Overview

This system uses **two complementary models** for different use cases:

### BackfillJob (Management Command)
- **Purpose**: Manual, on-demand backfills with detailed verification
- **Used by**: `resilient_backfill` management command
- **Features**: Checkpoint/resume, verification, detailed progress tracking
- **Admin**: `/admin/meetings/backfilljob/`
- **When to use**: One-off backfills, data validation, debugging

### BackfillProgress (Webhook-Triggered)
- **Purpose**: Automated daily updates via webhooks
- **Used by**: Webhook system and background tasks
- **Features**: Mode selection (full/incremental), job chaining
- **Admin**: `/admin/meetings/backfillprogress/`
- **When to use**: Automatically triggered by civic.band webhooks

**Important**: Both systems coordinate to prevent concurrent backfills for the same municipality. See "Concurrent Backfill Prevention" below.

## Features

- **Checkpoint/Resume**: Automatically saves progress every 1000 records. If interrupted, resumes from last checkpoint.
- **Retry Logic**: Automatically retries timeouts with exponential backoff (1s, 2s, 4s).
- **Per-Page Error Handling**: One bad page doesn't fail entire document or batch.
- **Verification**: Compares local counts vs API counts to ensure completeness.
- **Progress Tracking**: View progress in Django admin or database.

## Usage

### Basic Commands

```bash
# Backfill one municipality (agendas + minutes)
python manage.py resilient_backfill --subdomain=oakland.ca

# Backfill all municipalities
python manage.py resilient_backfill --subdomain=all

# Backfill only agendas
python manage.py resilient_backfill --subdomain=oakland.ca --document-type=agenda

# Resume failed jobs
python manage.py resilient_backfill --subdomain=all --resume

# Verify existing data without fetching
python manage.py resilient_backfill --subdomain=all --verify-only
```

### Custom Batch Size

For slower APIs or large municipalities, reduce batch size:

```bash
python manage.py resilient_backfill --subdomain=oakland.ca --batch-size=500
```

## Monitoring Progress

### Django Admin

Visit `/admin/meetings/backfilljob/` to view:
- Job status (pending, running, completed, failed, paused)
- Progress (pages fetched, created, updated)
- Verification results (expected vs actual counts)
- Error messages

### Database Queries

```python
from meetings.models import BackfillJob

# Check failed jobs
failed = BackfillJob.objects.filter(status="failed")
for job in failed:
    print(f"{job.municipality.subdomain}: {job.last_error}")

# Check jobs with missing data
incomplete = BackfillJob.objects.filter(
    status="completed", actual_count__lt=models.F("expected_count")
)
```

## Concurrent Backfill Prevention

To prevent data conflicts and wasted resources, the system automatically prevents concurrent backfills:

### How It Works

1. **BackfillJob (Management Command)**: Checks for active BackfillProgress jobs before starting
2. **BackfillProgress (Webhooks)**: Uses database locking to prevent duplicate jobs
3. **Shared Check**: Both systems check each other's status before proceeding

### When Concurrent Backfill is Detected

If you try to start a manual backfill while a webhook-triggered one is running (or vice versa):

```
⚠️  Backfill already in progress for oakland.ca (started: 2 minutes ago)
    Active job: BackfillProgress (incremental mode)
    You can check status in admin or wait for completion.
```

### Overriding (Use with Caution)

If a job is stuck and you need to force a new backfill:

```bash
# Mark stuck job as failed in admin first, then run:
python manage.py resilient_backfill --subdomain=oakland.ca
```

## Troubleshooting

### Job Failed with Timeout

Increase timeout or reduce batch size:
```bash
python manage.py resilient_backfill --subdomain=oakland.ca --batch-size=500
```

### Verification Failed (Missing Data)

Re-run backfill for that municipality:
```bash
python manage.py resilient_backfill --subdomain=oakland.ca
```

### Resume Interrupted Job

Jobs automatically checkpoint progress. Simply re-run:
```bash
python manage.py resilient_backfill --subdomain=oakland.ca --resume
```

The job will resume from the last cursor position.

## Architecture

### BackfillJob Model

Tracks progress and state for each municipality+document_type combination:
- `last_cursor`: Pagination cursor to resume from
- `pages_fetched`: Total pages fetched from API
- `pages_created`: New MeetingPage records created
- `pages_updated`: Existing MeetingPage records updated
- `expected_count`: Expected total from API
- `actual_count`: Actual count in local database
- `verified_at`: When verification was performed

### ResilientBackfillService

Core service class with:
- HTTP client with 120s timeout
- Exponential backoff retry (3 attempts)
- Checkpoint after each batch (1000 records)
- Per-page error handling
- Verification engine

## Differences from Original Backfill

The original `backfill_municipality_meetings` function:
- No checkpoint/resume capability
- Transaction rollbacks lose entire documents
- No verification of completeness
- 60s timeout (too short for large responses)

The resilient system adds:
- Checkpoint every 1000 records
- Per-page error handling
- Automatic verification
- 120s timeout with retry

Both systems can coexist. The resilient system is recommended for new backfills.
