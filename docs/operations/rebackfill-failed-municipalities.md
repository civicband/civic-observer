# Re-backfilling Failed Municipalities

## Problem

civic-observer is missing ~4.7M pages (38% of civic.band data) due to:
- 70 municipalities never indexed
- 14 municipalities indexed but with 0 pages (failed backfill)
- 22 municipalities with < 100 pages (likely incomplete)

## Solution

Use the `rebackfill_failed_municipalities` management command to automatically identify and queue backfill jobs for all problematic municipalities.

## Usage

### 1. Dry Run (Check What Will Be Backfilled)

```bash
python manage.py rebackfill_failed_municipalities --dry-run
```

This shows:
- Total municipalities that need re-backfill
- Breakdown: never indexed, zero pages, incomplete
- Sample list of municipalities
- **Does not enqueue any jobs**

### 2. Re-backfill All Failed Municipalities

```bash
python manage.py rebackfill_failed_municipalities
```

This will:
- Find all municipalities with `last_indexed IS NULL` OR `page_count < 100`
- Show a summary and ask for confirmation
- Enqueue backfill jobs to RQ queue "default"
- Each job has a 30-minute timeout

**Expected:**
- ~107 municipalities will be queued (70 never + 14 zero + 22 incomplete + 1 borderline)
- Each takes 5-15 minutes to complete
- Total time: 10-30 hours depending on RQ worker count

### 3. Re-backfill Only Never-Indexed Municipalities

```bash
python manage.py rebackfill_failed_municipalities --only-never-indexed
```

This targets only the 70 municipalities that were never indexed.

### 4. Re-backfill Only Zero-Page Municipalities

```bash
python manage.py rebackfill_failed_municipalities --only-zero-pages
```

This targets only the 14 municipalities that were indexed but have 0 pages.

### 5. Custom Threshold

```bash
python manage.py rebackfill_failed_municipalities --min-pages 1000
```

This will re-backfill any municipality with < 1,000 pages (more aggressive).

## Monitoring Progress

### RQ Dashboard

Visit `/django-rq/` in Django admin to see:
- Queued jobs
- Currently running jobs
- Failed jobs
- Completed jobs

### Database Queries

Check page count progress:

```sql
SELECT COUNT(*) FROM meetings_meetingpage;
```

Check failed municipalities:

```sql
SELECT
  CASE
    WHEN page_count = 0 THEN '0 pages (failed backfill)'
    WHEN page_count < 100 THEN '1-99 pages (likely incomplete)'
    WHEN page_count < 1000 THEN '100-999 pages'
    WHEN page_count < 10000 THEN '1,000-9,999 pages'
    ELSE '10,000+ pages'
  END as page_range,
  COUNT(*) as muni_count,
  SUM(page_count) as total_pages
FROM (
  SELECT
    m.id,
    COUNT(mp.id) as page_count
  FROM municipalities_muni m
  LEFT JOIN meetings_meetingdocument md ON md.municipality_id = m.id
  LEFT JOIN meetings_meetingpage mp ON mp.document_id = md.id
  GROUP BY m.id
) subq
GROUP BY page_range
ORDER BY MIN(page_count);
```

## Expected Results

**Before:**
- Total pages: 7,560,422
- Missing: 4,699,242 (38%)

**After:**
- Total pages: ~12,259,664
- Missing: ~0 (0%)

## Troubleshooting

### Jobs Are Failing

Check RQ dashboard for error messages. Common issues:
- civic.band API timeout (increase job timeout)
- civic.band API rate limiting (reduce worker count)
- Database connection issues (check connection pool)

### Jobs Are Stuck

- Check if RQ workers are running: `docker-compose ps worker`
- Restart workers if needed: `docker-compose restart worker`
- Check worker logs: `docker-compose logs worker`

### Still Missing Data After Backfill

Some municipalities may legitimately have no data on civic.band. Verify by checking:

```bash
curl "https://civic.band/api/v1/municipalities/{subdomain}/"
```

If civic.band has no data, the municipality should be marked as indexed with 0 pages (this is correct).

## Performance Note: Slow COUNT Queries

The query `SELECT COUNT(*) FROM meetings_meetingpage` takes ~50 seconds on 7.5M rows. This is **expected behavior** for PostgreSQL on large tables.

### Why Is It Slow?

PostgreSQL uses MVCC (Multi-Version Concurrency Control), which means COUNT(*) must:
- Perform a sequential scan of the entire table
- Check row visibility for each row
- Cannot use indexes for simple COUNT(*)

### Solutions

1. **Use approximate count** (instant, ~95% accurate):
```sql
SELECT reltuples::bigint AS estimate
FROM pg_class
WHERE relname = 'meetings_meetingpage';
```

2. **Cache the count** in Redis with hourly updates

3. **Accept it** - exact counts on 7.5M+ row tables are slow in PostgreSQL

### Table Maintenance

Run VACUUM ANALYZE periodically to clean up dead rows:

```bash
psql $DATABASE_URL -c "VACUUM ANALYZE meetings_meetingpage;"
```

This cannot be run through pgweb (maintenance command, not a query).

## Related Issues

- Missing 4.7M pages: #TBD
- Slow search performance: #69, #71, #72
