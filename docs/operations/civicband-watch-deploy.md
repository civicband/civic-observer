# CivicBand Watch Deployment and Operations

CivicBand Watch is a staff-only app inside civic-observer. It ingests the F5Bot
Reddit feed, groups mentions into threads, and surfaces related municipal
meetings. No new infrastructure is required: it uses the existing PostgreSQL,
Redis, django-rq worker, and cron.

## Milestone 1: Ingest

### 1. Apply migrations

```bash
python manage.py migrate
```

### 2. Configure the feed URL

Set `F5BOT_FEED_URL` in the deployment environment to the private F5Bot feed
URL. The URL is a secret: keep it out of tickets, logs, screenshots, and this
repository.

Optional settings:

- `WATCH_DATA_DIR` — cache directory for downloaded datasets (defaults to
  `.cache/watch` under the project directory).
- `WATCH_INGEST_LOCK_SECONDS` — how long the Redis ingest lock is held
  (default `3600`).

### 3. Run an ingest manually

```bash
python manage.py ingest_f5bot
```

The command polls up to `--max-pages 20` feed pages by default, stops at the
first page already seen, and records every run in `IngestRun` (`/admin/` →
Watch → Ingest runs). Failed runs are recorded too, with an error type rather
than feed contents or credentials.

Useful options:

```bash
python manage.py ingest_f5bot --max-pages 500   # backfill
python manage.py ingest_f5bot --file page.json  # ingest a saved feed page
```

### 4. Add the hourly cron entry

The crontab itself lives in the `public-works` deployment repository, not in
this repo; this repo ships only the management command. Add the entry there:

```cron
5 * * * * cd /home/deploy/civic-observer && docker compose -f docker-compose.production.yml run --rm web-blue python manage.py ingest_f5bot --max-pages 20
```

One feed page covers roughly 12.5 hours, so hourly polling stays well ahead.
The command exits nonzero on a failed run, so cron mail or monitoring surfaces
feed errors.

### 5. Verify

- `IngestRun` shows a recent successful run with no error.
- Threads and mentions appear in the admin. Mentions and ingest runs are
  read-only; `raw_json` is available on a mention's detail page but is not
  shown in the changelist.
