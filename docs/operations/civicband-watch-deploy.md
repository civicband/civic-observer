# CivicBand Watch Deployment and Operations

CivicBand Watch is a staff-only app inside civic-observer. It ingests the F5Bot
Reddit feed, groups mentions into threads, and surfaces related municipal
meetings. No new infrastructure is required: it uses the existing PostgreSQL,
Redis, django-rq worker, and cron.

## First-run order

Run these in order on a fresh deployment. Each step is idempotent, so a failed
or interrupted step can be re-run.

Run every `python manage.py` command in this guide inside the web container. The
examples show the bare command for brevity; from the deployment host the
wrapper is:

```bash
docker compose -f docker-compose.production.yml run --rm web-blue python manage.py <command>
```

1. Apply migrations:

   ```bash
   python manage.py migrate
   ```

2. Load the place datasets:

   ```bash
   python manage.py load_watch_gazetteer --download
   python manage.py load_watch_governments --source-dir /path/to/census-governments
   python manage.py load_watch_geonames --download
   ```

   `load_watch_gazetteer` downloads the current Gazetteer files (override the
   year with `--year`). `load_watch_governments` expects
   `Govt_Units_2022_Final.xlsx` in `--source-dir` (or in `WATCH_DATA_DIR` when
   `--source-dir` is omitted); use `--file` if the download has a different
   name. `load_watch_geonames` downloads `cities500.txt`; use
   `--min-population` to change the threshold for non-US, non-Canadian lookup
   cities.

3. Optionally link Wikidata:

   ```bash
   python manage.py load_watch_wikidata
   ```

   This is optional and adds external identifiers to places. Use `--dry-run`
   to report link counts without saving, or `--file` to read a saved SPARQL
   response instead of querying Wikidata.

4. Seed subreddits and link them to places:

   ```bash
   python manage.py seed_watch_subreddits
   python manage.py link_watch_munis
   python manage.py classify_watch_subreddits
   ```

   `seed_watch_subreddits` loads the subreddit label CSV and manual aliases
   (`--subreddits-csv`, `--aliases-csv`, `--skip-aliases` to skip aliases,
   `--refresh-labels` to overwrite existing labels). `link_watch_munis` links
   subreddits to municipalities (`--dry-run`, `--limit`).
   `classify_watch_subreddits` assigns kind and place from those links
   (`--reclassify`, `--limit`).

5. Configure the feed URL and email recipients, then add the cron entries from
   [Milestone 6](#milestone-6-health-alerts-suggestions-and-reports).

## Environment variables

Set these in the deployment environment (see `.env.example` for the full
list):

| Variable | Status | Purpose |
| --- | --- | --- |
| `F5BOT_FEED_URL` | Required | Private F5Bot Reddit feed URL. `ingest_f5bot` errors when it is unset. Treated as a secret. |
| `WATCH_DATA_DIR` | Defaulted (`.cache/watch`) | Cache directory for downloaded datasets. Set only to override. |
| `WATCH_ALERT_EMAIL` | Empty allowed | Comma-separated recipients for the once-per-day stale-ingest alert. Empty disables the alert email. |
| `WATCH_SUMMARY_EMAIL` | Empty allowed | Comma-separated recipients for the weekly summary. Empty skips sending. |
| `WATCH_STALE_HOURS` | Defaulted (`6`) | Hours without a successful ingest before the pipeline counts as stale. |
| `WATCH_INGEST_LOCK_SECONDS` | Optional | How long the Redis ingest lock is held (default `3600`). |
| `WATCH_LLM_ENABLED` | Optional | Enables the optional LLM place pass. Off by default. |
| `WATCH_LLM_BASE_URL` | Optional | OpenAI-compatible endpoint for the LLM pass. |
| `WATCH_LLM_API_KEY` | Optional | API key for the LLM endpoint. |
| `WATCH_LLM_MODEL` | Optional | Model name for the LLM pass. |
| `WATCH_LLM_TIMEOUT` | Optional | LLM request timeout in seconds (default `20`). |

`F5BOT_FEED_URL` is a secret. Never paste it into tickets, chat, logs,
screenshots, commit messages, or this repository. If it leaks, rotate the feed
in F5Bot and update the deployment environment.

The LLM pass stays off by default and the deterministic extractor works without
it. Enabling `WATCH_LLM_ENABLED` sends thread titles and snippets to the
configured off-host endpoint, so confirm that is acceptable before turning it
on.

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

The command polls up to `--max-pages 20` feed pages by default, stops after the
first page that contains an already-seen item, and records every run in
`IngestRun` (`/admin/` → Watch → Ingest runs). Failed runs are recorded too,
with an error type rather than feed contents or credentials.

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
- A run with `new=0`, `skipped>0`, and `error=""` is the signal to check the f5bot feed format.
- Threads and mentions appear in the admin. Mentions and ingest runs are
  read-only; `raw_json` is available on a mention's detail page but is not
  shown in the changelist.

## Milestone 5: place extraction

### Backfill existing threads

After a release that includes place extraction, enqueue enrichment for threads
created before it existed. This is the required post-deploy step:

```bash
python manage.py backfill_watch_threads
```

The command selects threads with no `ThreadPlace` rows, newest first, and
enqueues one enrichment job per thread. It only enqueues work; the django-rq
worker must be running for places and meeting hits to appear. Re-run the whole
corpus after changing extraction rules:

```bash
python manage.py backfill_watch_threads --all
```

Scope a run with `--since YYYY-MM-DD` (threads last seen on or after that date),
`--limit N`, and `--dry-run`. It prints `enqueued`, `skipped`, and `failed`
counts and reports every thread whose job could not be queued.

### Refresh meeting hits

Enrichment writes places; meeting hits are cached separately. After a place
change, refresh them:

```bash
python manage.py refresh_watch_hits --thread <thread-uuid>
python manage.py refresh_watch_hits --all
python manage.py refresh_watch_hits --all --limit 500
```

## Milestone 6: health alerts, suggestions, and reports

Add these entries to the crontab in `public-works`. This block replaces the
Milestone 1 ingest line: keep one `ingest_f5bot` entry and do not add a second
identical line.

```cron
5 * * * * cd /home/deploy/civic-observer && docker compose -f docker-compose.production.yml run --rm web-blue python manage.py ingest_f5bot --max-pages 20
20 * * * * cd /home/deploy/civic-observer && docker compose -f docker-compose.production.yml run --rm web-blue python manage.py check_watch_health
30 3 * * * cd /home/deploy/civic-observer && docker compose -f docker-compose.production.yml run --rm web-blue python manage.py suggest_watch_subreddits
0 8 * * 1 cd /home/deploy/civic-observer && docker compose -f docker-compose.production.yml run --rm web-blue python manage.py watch_weekly_summary
```

- `ingest_f5bot` runs at five minutes past every hour.
- `check_watch_health` runs at twenty minutes past every hour, after the ingest
  has finished.
- `suggest_watch_subreddits` runs nightly and proposes labels from recent
  thread evidence.
- `watch_weekly_summary` runs Monday morning and emails the weekly summary.

### Health alerts

`check_watch_health` owns the once-per-day stale alert email and the exit code.
It emails at most once per local day, and only when the pipeline is stale and
`WATCH_ALERT_EMAIL` has recipients: no successful ingest has ever completed, or
the last successful run finished more than `WATCH_STALE_HOURS` ago.

A quiet pipeline (no run in the last 24 hours added mentions) does not send
email by itself; it only makes the command exit nonzero. Keep exit-code
monitoring enabled for this cron entry even if cron mail is disabled, or quiet
failures go unnoticed.

Do not configure the cron wrapper or cron's own mail handling to also email on
a nonzero exit. The stale case would then produce two notifications for the
same failure. Use exit-code monitoring for nonzero exits and let the command
own the stale email.

### Subreddit suggestions

`suggest_watch_subreddits` proposes places for subreddits using recent subject
thread evidence. Suggested rows get `source="text_evidence"` and medium
confidence. The command never overwrites a manual label, never changes a muted
subreddit, and leaves a non-empty note untouched. A manual label that points at
a different place is reported as a conflict instead of being changed.

Tune it with `--days` (lookback window), `--min-threads`, and `--share`, and
preview changes with `--dry-run`. Review suggestions under
`/admin/` → Watch → Subreddits; accept one with the "Accept suggested
classification" action.

### Weekly summary

`watch_weekly_summary` emails replied and dismissed counts, backlog size, and
the newest replied threads with triage links. It skips sending when
`WATCH_SUMMARY_EMAIL` is empty, so the cron entry is safe to install before
recipients are configured.

### Keyword report

```bash
python manage.py watch_keyword_report --days 30
python manage.py watch_keyword_report --days 30 --json
```

The report shows per-keyword mentions, distinct threads, threads with a covered
subject place, and replied threads. `--json` emits machine-readable output for
dashboards or ad-hoc analysis.

## Operational commands

### Backfill ingest and threads after a deploy

Pull history and then enrich the existing threads:

```bash
python manage.py ingest_f5bot --max-pages 500
python manage.py backfill_watch_threads
```

`ingest_f5bot` stops after the first page that contains an already-seen item
(the page is still processed). A deep backfill therefore reaches older history
only if it runs before the hourly cron has consumed the feed's first page. Once
hourly ingest is running, a `--max-pages 500` run typically processes page 1
and stops. Check `IngestRun.pages` to confirm how deep a run actually went;
`pages=1` means it stopped on the first page.

`backfill_watch_threads` only enqueues enrichment jobs; it does not extract
places itself. The django-rq worker must be running, or the jobs sit in the
queue. Follow the backfill with `refresh_watch_hits` if meeting hits need
rebuilding.

### Re-run enrichment for one thread

From the admin, open `/admin/` → Watch → Threads, select the single thread,
choose "Rerun enrichment for selected threads" from the action menu, and click
Go. This enqueues one enrichment job on the worker; the same action works on a
batch. Use it after changing extraction rules or after correcting a thread's
subreddit.

If meeting hits look stale, refresh them for that thread:

```bash
python manage.py refresh_watch_hits --thread <thread-uuid>
```

### Measure extraction quality

```bash
python manage.py watch_extraction_report
python manage.py watch_extraction_report --since 2026-01-01
python manage.py watch_extraction_report --json
```

The report compares extracted places against operator labels. Manual rows are
ground truth and are excluded from the comparison.

### Inspect ingest health manually

```bash
python manage.py check_watch_health
```

The output reports `stale`, `quiet`, `hours_since_success`, `alerts_today`, and
whether an alert email was sent. `alerts_today` is the number of new mentions
from ingest runs started today, not a count of emails sent. The same signals
appear on the Watch dashboard in the admin.
