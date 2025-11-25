# Saved Search Notifications

This document describes the saved search notification system and how to set up scheduled digest emails.

## Overview

The saved search system allows users to save search configurations and receive notifications when new matching pages are ingested. There are three notification frequencies:

- **Immediate**: Email sent as soon as new results appear (triggered automatically after webhook processing)
- **Daily**: Results batched and sent once per day
- **Weekly**: Results batched and sent once per week

## Architecture

### Notification Flow

1. **Webhook Processing** → **Backfill Task** → **Immediate Notifications**
   - When civic.band sends a webhook about new/updated municipalities
   - The backfill task ingests new meeting pages
   - At the end of backfill, `check_all_immediate_searches()` is called
   - All saved searches with `notification_frequency="immediate"` are checked
   - Emails are sent immediately for any new matches

2. **Scheduled Daily Digests**
   - Users with `notification_frequency="daily"` accumulate pending results
   - The `send_daily_digests` management command sends a single email per user
   - Email includes all pending searches with new results
   - Should be scheduled to run once per day via cron

3. **Scheduled Weekly Digests**
   - Users with `notification_frequency="weekly"` accumulate pending results
   - The `send_weekly_digests` management command sends a single email per user
   - Email includes all pending searches with new results
   - Should be scheduled to run once per week via cron

## Setup Instructions

### 1. Immediate Notifications

Immediate notifications are **automatically enabled** and require no additional setup. They are triggered by the webhook → backfill workflow in `meetings/tasks.py:backfill_municipality_meetings_task()`.

### 2. Daily Digest Notifications

Set up a cron job to run the daily digest command:

```bash
# Edit crontab
crontab -e

# Add this line to run daily at 9 AM
0 9 * * * cd /path/to/civic-observer && uv run python manage.py send_daily_digests >> /var/log/civic-observer/daily-digests.log 2>&1
```

**Alternative with Docker:**
```bash
# Run daily at 9 AM
0 9 * * * docker-compose exec -T web python manage.py send_daily_digests >> /var/log/civic-observer/daily-digests.log 2>&1
```

### 3. Weekly Digest Notifications

Set up a cron job to run the weekly digest command:

```bash
# Edit crontab
crontab -e

# Add this line to run every Monday at 9 AM
0 9 * * 1 cd /path/to/civic-observer && uv run python manage.py send_weekly_digests >> /var/log/civic-observer/weekly-digests.log 2>&1
```

**Alternative with Docker:**
```bash
# Run every Monday at 9 AM
0 9 * * 1 docker-compose exec -T web python manage.py send_weekly_digests >> /var/log/civic-observer/weekly-digests.log 2>&1
```

## Management Commands

### `send_daily_digests`

Send daily digest emails to all users with pending results.

```bash
# Local development
uv run python manage.py send_daily_digests

# Docker
docker-compose exec web python manage.py send_daily_digests
```

**Output:**
```
Starting daily digest task...
Daily digests complete: 5 emails sent for 12 searches
```

### `send_weekly_digests`

Send weekly digest emails to all users with pending results.

```bash
# Local development
uv run python manage.py send_weekly_digests

# Docker
docker-compose exec web python manage.py send_weekly_digests
```

**Output:**
```
Starting weekly digest task...
Weekly digests complete: 3 emails sent for 8 searches
```

## Email Templates

Email templates are located in `templates/email/`:

- `digest_update.html` - HTML version of digest emails
- `digest_update.txt` - Plain text version of digest emails

Both templates include:
- All pending saved searches for the user
- Search parameters (municipalities, states, date range, etc.)
- Links to view full search results

## Monitoring

### Check Pending Notifications

```python
from searches.models import SavedSearch

# Count searches with pending results
pending_count = SavedSearch.objects.filter(has_pending_results=True).count()

# Group by frequency
from django.db.models import Count

SavedSearch.objects.filter(has_pending_results=True).values(
    "notification_frequency"
).annotate(count=Count("id"))
```

### View Recent Notifications

```python
from searches.models import SavedSearch

# Searches with recent notifications
recent = SavedSearch.objects.filter(last_notification_sent__isnull=False).order_by(
    "-last_notification_sent"
)[:10]

for s in recent:
    print(f"{s.name}: {s.last_notification_sent}")
```

### Check Digest Send History

The digest commands return statistics that can be logged:

```python
from searches.tasks import send_daily_digests

result = send_daily_digests()
# Returns: {'emails_sent': 5, 'searches_notified': 12}
```

## Troubleshooting

### No emails being sent

1. Check email configuration (see `docs/email-configuration.md`)
2. Verify Postmark API key is set
3. Check that searches have `has_pending_results=True`
4. Verify cron jobs are running: `crontab -l`
5. Check cron logs for errors

### Immediate notifications not working

1. Verify webhook processing is working
2. Check that `check_all_immediate_searches()` is being called in backfill task
3. Check RQ worker is running: `docker-compose ps worker`
4. Check worker logs: `docker-compose logs worker`

### Digest emails sent at wrong time

1. Verify cron job schedule: `crontab -l`
2. Check server timezone: `date`
3. Adjust cron schedule as needed

## Performance Considerations

- Immediate notifications are checked in bulk after each backfill (efficient)
- Digest tasks batch all pending searches per user (single email per user)
- Email sending is handled by Postmark (reliable delivery)
- Database queries use `select_related()` and `prefetch_related()` for efficiency

## Manual Testing

The notification system includes **Django admin actions** for easy testing directly from the admin interface.

### Django Admin Actions

In the Django admin at `/admin/searches/savedsearch/`, select one or more saved searches and use these actions from the dropdown:

#### 1. Check for new results and send notifications
- Checks selected searches for new results
- For immediate frequency: Sends notification if new results found
- For daily/weekly: Marks as having pending results
- Shows summary of actions taken

**Use case:** Test the standard notification workflow as it would happen after a webhook/backfill.

#### 2. Send test notification (immediate only)
- Sends a test email with current results (up to 10 pages)
- Useful for testing email templates and delivery
- Works regardless of whether there are "new" results

**Use case:** Verify email formatting and delivery without needing new data.

#### 3. Mark as having pending results (for testing digests)
- Manually flags selected searches as having pending results
- Perfect for testing daily/weekly digest emails
- After marking, run `send_daily_digests` or `send_weekly_digests` command

**Use case:** Set up digest testing without waiting for actual new results.

#### 4. Clear pending results flag
- Removes pending results flag from selected searches
- Useful for cleanup after testing

**Use case:** Reset state after testing.

### Quick Testing Workflow

#### Test Immediate Notifications
1. Go to `/admin/searches/savedsearch/`
2. Select one or more saved searches with **immediate** frequency
3. Choose **"Send test notification (immediate only)"** from the Actions dropdown
4. Click "Go"
5. Check your email for the notification

#### Test Daily/Weekly Digests
1. Go to `/admin/searches/savedsearch/`
2. Select saved searches with **daily** or **weekly** frequency
3. Choose **"Mark as having pending results (for testing digests)"** from Actions
4. Click "Go"
5. Run the digest command to send emails:
   ```bash
   # For daily digests
   uv run python manage.py send_daily_digests

   # For weekly digests
   uv run python manage.py send_weekly_digests
   ```
6. Check your email for the digest

#### Test the Full Workflow
1. Create or update meeting pages (via webhook or admin backfill action)
2. Go to `/admin/searches/savedsearch/`
3. Select saved searches
4. Choose **"Check for new results and send notifications"** from Actions
5. Click "Go"
6. For immediate: Check email immediately
7. For daily/weekly: Run the appropriate digest command later

### Testing with Docker

When using Docker, run digest commands with:

```bash
# Using just
just manage send_daily_digests
just manage send_weekly_digests

# Using docker-compose directly
docker-compose run --rm utility python manage.py send_daily_digests
docker-compose run --rm utility python manage.py send_weekly_digests
```
