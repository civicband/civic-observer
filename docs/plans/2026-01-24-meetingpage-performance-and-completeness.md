# MeetingPage Performance and Data Completeness Fix

**Date**: 2026-01-24
**Status**: Design Complete, Ready for Implementation
**Context**: MeetingPage table is missing 50% of expected data (6M/12M pages) and queries are extremely slow

---

## Problem Statement

### Data Completeness Issues
- **Expected**: 12M+ MeetingPage records
- **Actual**: 6M records (50% missing)
- **Impact**: Users cannot find documents that should exist

### Performance Issues
- **Full-text search**: Extremely slow even with GIN index
- **Count queries**: `count(*)` takes 30+ seconds on 6M rows
- **Admin aggregations**: Dashboard queries timeout

### Root Causes

**Missing Data:**
1. Pagination may fail silently (cursor issues)
2. Row processing errors are logged but swallowed
3. Transaction rollbacks lose entire documents if one page fails
4. No verification that backfill completed successfully
5. No checkpoint/resume capability for large municipalities

**Slow Queries:**
1. GIN index may be bloated or misconfigured
2. PostgreSQL not using parallel workers for full-table scans
3. No materialized views for common aggregations
4. Statistics may be stale or insufficient

---

## Solution: Two-Phase Approach

### Phase 1: Resilient Backfill System (Weeks 1-2)

Fix data completeness with verification and resume capability.

#### Architecture Components

**1. BackfillJob Model** - Track progress per municipality

```python
class BackfillJob(TimeStampedModel):
    municipality = ForeignKey(Muni)
    document_type = CharField(max_length=10)  # 'agenda' or 'minutes'

    # State tracking
    status = CharField(
        choices=[
            ("pending", "Pending"),
            ("running", "Running"),
            ("completed", "Completed"),
            ("failed", "Failed"),
            ("paused", "Paused"),
        ]
    )

    # Progress tracking (checkpoint)
    last_cursor = TextField(blank=True)  # Resume from here
    pages_fetched = IntegerField(default=0)
    pages_created = IntegerField(default=0)
    pages_updated = IntegerField(default=0)
    errors_encountered = IntegerField(default=0)

    # Verification
    expected_count = IntegerField(null=True)  # From API
    actual_count = IntegerField(null=True)  # From local DB
    verified_at = DateTimeField(null=True)

    # Error details
    last_error = TextField(blank=True)
    retry_count = IntegerField(default=0)
```

**2. ResilientBackfillService** - Core fetching logic

Key features:
- **120s timeout** (up from 60s) - Large responses need more time
- **Exponential backoff retry** - Automatic retry on timeouts (2^n seconds)
- **Checkpoint after each batch** - Save progress every 1000 rows
- **Resume capability** - Restart from last cursor if interrupted
- **Per-page error handling** - One bad page doesn't fail entire document
- **Verification engine** - Compare local count vs API count after completion

**3. Verification Strategy**

After each backfill:
1. Fetch total count from API metadata
2. Count local records for municipality + document_type
3. Compare: if actual < expected by >1%, mark job as FAILED
4. Log discrepancies for investigation

**4. Management Commands**

```bash
# Backfill one municipality
python manage.py resilient_backfill --subdomain=oakland

# Backfill all municipalities
python manage.py resilient_backfill --subdomain=all

# Resume failed jobs
python manage.py resilient_backfill --subdomain=all --resume

# Verify existing data without fetching
python manage.py resilient_backfill --subdomain=all --verify-only

# Custom batch size for slower APIs
python manage.py resilient_backfill --subdomain=oakland --batch-size=500
```

#### Immediate Performance Wins (While Backfilling)

**1. Materialized View for Aggregations**

```sql
CREATE MATERIALIZED VIEW meetings_page_stats AS
SELECT
    md.municipality_id,
    md.document_type,
    DATE_TRUNC('month', md.meeting_date) AS month,
    COUNT(mp.id) AS page_count,
    COUNT(DISTINCT md.id) AS document_count
FROM meetings_meetingpage mp
JOIN meetings_meetingdocument md ON mp.document_id = md.id
GROUP BY md.municipality_id, md.document_type, DATE_TRUNC('month', md.meeting_date);
```

Benefits:
- Count queries: Instant instead of 30+ seconds
- Dashboard aggregations: Pre-computed
- Refresh periodically (daily/hourly) vs on every query

**2. Index Statistics Update**

```sql
-- Better query planning
ALTER TABLE meetings_meetingpage
ALTER COLUMN search_vector SET STATISTICS 1000;

ALTER TABLE meetings_meetingpage
ALTER COLUMN document_id SET STATISTICS 1000;

ANALYZE meetings_meetingpage;
```

**3. PostgreSQL Configuration**

```conf
work_mem = 256MB                    # For sorting/aggregation
maintenance_work_mem = 2GB          # For index creation/VACUUM
shared_buffers = 4GB                # Cache frequently accessed data
effective_cache_size = 12GB         # Tell planner about available RAM

gin_pending_list_limit = 4MB       # Faster GIN index updates
random_page_cost = 1.1              # SSDs are fast
effective_io_concurrency = 200      # Parallel I/O
```

---

### Phase 2: Full-Corpus Search Optimization (Weeks 3-4)

**Prerequisites:**
- Phase 1 complete: All 12M pages backfilled and verified
- Data quality confirmed: No missing municipalities or date ranges

**Strategy:** Optimize for whole-corpus search without partitioning

Since users regularly search the entire corpus without date filters, partitioning by `meeting_date` would **hurt** performance (forces scanning all partitions). Instead, optimize the single table for fast parallel scans.

#### Option 1: GIN Index Optimization

**Problem:** Current GIN index may be bloated or using suboptimal settings.

**Solution:** Rebuild with optimal configuration

```sql
-- Drop old index
DROP INDEX IF EXISTS meetings_meetingpage_search_vector_idx;

-- Create optimized GIN index
CREATE INDEX meetings_meetingpage_search_vector_optimized_idx
ON meetings_meetingpage
USING GIN (search_vector)
WITH (
    fastupdate = off,           -- No pending list, always consistent
    gin_pending_list_limit = 0  -- Force immediate index updates
);

-- Increase statistics
ALTER TABLE meetings_meetingpage
ALTER COLUMN search_vector SET STATISTICS 1000;

ANALYZE meetings_meetingpage;
```

**Key settings:**
- `fastupdate = off` - Eliminates pending list that slows searches
- Index is larger but searches are consistently fast
- No "cleanup" penalty during searches

**Additional Covering Indexes:**

```sql
-- Covering index for document_id + search results
-- Avoids heap lookups for common queries
CREATE INDEX meetings_meetingpage_document_covering_idx
ON meetings_meetingpage (document_id)
INCLUDE (id, page_number, text);
```

#### Option 2: Parallel Query Tuning

**Problem:** PostgreSQL may not be using parallel workers for full-table scans.

**Solution:** Configure aggressive parallelism

```conf
# Worker configuration
max_parallel_workers_per_gather = 8    # Use 8 workers per query
max_parallel_workers = 16               # Total parallel workers
max_worker_processes = 16               # Background worker pool

# Cost thresholds (lower = more aggressive)
parallel_setup_cost = 100               # Cost of starting workers
parallel_tuple_cost = 0.01              # Cost per tuple
min_parallel_table_scan_size = 8MB      # Parallelize tables > 8MB
min_parallel_index_scan_size = 512kB    # Parallelize index scans > 512KB

# Work memory per worker
work_mem = 256MB
```

**Application-level parallel hints:**

```python
from contextlib import contextmanager
from django.db import connection


@contextmanager
def parallel_search_mode():
    """Enable aggressive parallelism for search queries."""
    with connection.cursor() as cursor:
        cursor.execute("SHOW max_parallel_workers_per_gather;")
        old_workers = cursor.fetchone()[0]

        cursor.execute("SET max_parallel_workers_per_gather = 8;")
        cursor.execute("SET parallel_setup_cost = 50;")

        try:
            yield
        finally:
            cursor.execute(f"SET max_parallel_workers_per_gather = {old_workers};")


# Usage
with parallel_search_mode():
    results = MeetingPage.objects.filter(search_vector=query)[:100]
```

#### Verification and Benchmarking

**Benchmark Command:**

```bash
python manage.py benchmark_search --query=budget
```

Checks:
1. Query plan (EXPLAIN ANALYZE)
2. Parallel worker usage
3. Index scan statistics
4. Execution time

**What to look for:**
```
Gather  (cost=1000.00..250000.00 rows=100)
  Workers Planned: 8          <- Good! Using parallelism
  Workers Launched: 8         <- All workers started
  ->  Parallel Bitmap Heap Scan on meetings_meetingpage
        ->  Bitmap Index Scan on search_vector_optimized_idx
```

If `Workers Planned: 0`, parallelism is not being used.

---

## Why Not Partition?

Initial design considered partitioning by `meeting_date` (see `docs/PAGE_PARTITIONING.md`), but this is **wrong for full-corpus search**:

**Partitioning downsides for full-corpus search:**
- Every query scans all 36 partitions (no pruning without date filters)
- More coordination overhead vs single table
- PostgreSQL can parallelize single table scans more efficiently

**When partitioning makes sense:**
- Most queries have date filters (enables partition pruning)
- Need archival strategy (detach old partitions)
- Maintenance windows are too long (VACUUM entire table)

**Our use case:**
- Full-corpus search is PRIMARY use case
- Single optimized table + parallel scanning is faster

**Note:** We may revisit partitioning in the future for:
- Faster VACUUM/ANALYZE (maintenance)
- Archival of old data (detach partitions >2 years)
- But only AFTER confirming parallel full-scans are optimized

---

## Success Metrics

### Phase 1 (Data Completeness)
- **Data completeness**: 12M pages loaded (100% of expected)
- **Verification**: All municipalities show expected_count == actual_count
- **Resilience**: Failed jobs can resume from checkpoint
- **Count queries**: <1s via materialized view (down from 30s+)

### Phase 2 (Search Performance)
- **Full-text search**: <2s for top 100 results (down from 10s+)
- **Parallel execution**: EXPLAIN shows 8 workers used
- **Index efficiency**: GIN index scans at optimal speed
- **No regression**: Document fetching remains fast

---

## Implementation Roadmap

### Week 1: Resilient Backfill Foundation
1. Create BackfillJob model and migration
2. Implement ResilientBackfillService with checkpointing
3. Add verification engine
4. Create management command
5. Test on 1-2 small municipalities

### Week 2: Full Backfill + Immediate Performance
1. Run resilient backfill on all municipalities
2. Monitor and resume failed jobs
3. Verify data completeness (12M pages)
4. Create materialized view for aggregations
5. Update index statistics
6. Apply PostgreSQL config tuning

### Week 3: GIN Index Optimization
1. Analyze current GIN index (size, bloat, scans)
2. Create optimized GIN index with fastupdate=off
3. Drop old index
4. Run benchmark tests
5. Create covering indexes for common patterns

### Week 4: Parallel Query Tuning
1. Configure PostgreSQL parallel worker settings
2. Create parallel_search_mode context manager
3. Update search views to use parallel hints
4. Run benchmark tests
5. Create monitoring dashboard for query performance
6. Document best practices for search queries

---

## Rollback Plan

### Phase 1 Rollback
- BackfillJob is additive - can be ignored if unused
- Old backfill service (`meetings.services.backfill_municipality_meetings`) remains functional
- Materialized view can be dropped without impact

### Phase 2 Rollback
- Keep old GIN index until new one is verified
- Parallel settings can be reverted in postgresql.conf
- No schema changes, only configuration

---

## Monitoring and Maintenance

### Ongoing Monitoring

**1. Data Completeness Dashboard**
```sql
-- Check for municipalities with missing data
SELECT
    municipality_id,
    document_type,
    expected_count,
    actual_count,
    expected_count - actual_count AS missing
FROM meetings_backfilljob
WHERE status = 'completed'
  AND actual_count < expected_count;
```

**2. Search Performance Tracking**
```sql
-- Track search query performance over time
SELECT
    query,
    calls,
    mean_exec_time,
    max_exec_time,
    stddev_exec_time
FROM pg_stat_statements
WHERE query LIKE '%search_vector%'
ORDER BY mean_exec_time DESC
LIMIT 20;
```

### Maintenance Tasks

**Daily:**
- Refresh materialized view: `REFRESH MATERIALIZED VIEW CONCURRENTLY meetings_page_stats;`

**Weekly:**
- Check for failed backfill jobs: Review `BackfillJob.objects.filter(status='failed')`
- Review slow query log

**Monthly:**
- Run `ANALYZE` on meetings_meetingpage
- Check index bloat and rebuild if >30%
- Review PostgreSQL config for optimization opportunities

---

## Open Questions and Future Work

### Immediate Questions
- What is the actual API response format for counts? (Need to verify `filtered_table_rows_count` vs `count` field)
- Are there rate limits on civic.band API we should respect?
- Should backfill run automatically on schedule or only manual trigger?

### Future Enhancements
- **Auto-resume daemon**: Background process that automatically resumes failed jobs
- **Incremental backfill**: Only fetch pages added since last backfill
- **Webhook-triggered backfill**: Real-time updates when civic.band adds new documents
- **Partitioning (optional)**: Revisit for maintenance/archival if VACUUM becomes problematic

---

## Conclusion

This two-phase approach fixes both data completeness and performance:

**Phase 1** ensures we have all 12M pages with verification and resilience.
**Phase 2** optimizes full-corpus search without the overhead of partitioning.

The key insight: Partitioning hurts performance when most queries scan the whole table. Instead, optimize the single table for parallel scanning with a properly configured GIN index.

Expected improvements:
- ✅ 100% data completeness (12M pages)
- ✅ Count queries: 30s → <1s (materialized views)
- ✅ Full-text search: 10s+ → <2s (GIN optimization + parallelism)
- ✅ Resilient backfill system with checkpoint/resume
- ✅ Verification that catches missing data

**Next Steps:** Move to implementation planning (use `superpowers:writing-plans` skill).
