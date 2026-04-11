# Timeboxed PostgreSQL FTS Diagnosis Plan

**Date**: 2026-04-10
**Timebox**: 2 hours maximum
**Goal**: Determine if PostgreSQL FTS can be fixed to deliver sub-second search at 12M rows, or if we need to move to Quickwit

---

## Context

Despite extensive optimization (GIN indexes, no ts_rank, Redis caching, autovacuum tuning, JIT off), all search queries are returning 5s+. This is unexpectedly slow — GIN index lookups on 12M rows with no rank computation should be sub-second. Something else is likely wrong.

## Diagnosis Steps

### Step 1: Verify the Query Plan (15 min)

Connect to the production database and run `EXPLAIN (ANALYZE, BUFFERS, TIMING)` on the actual search query being executed. We need to see:

```sql
-- Basic FTS query (the most common case)
EXPLAIN (ANALYZE, BUFFERS, TIMING, FORMAT TEXT)
SELECT mp.*, md.*, m.*
FROM meetings_meetingpage mp
JOIN meetings_meetingdocument md ON mp.document_id = md.id
JOIN municipalities_muni m ON md.municipality_id = m.id
WHERE mp.search_vector @@ websearch_to_tsquery('simple', 'police')
ORDER BY md.meeting_date DESC
LIMIT 20 OFFSET 0;

-- Also run the count query separately (this may be the real bottleneck)
EXPLAIN (ANALYZE, BUFFERS, TIMING, FORMAT TEXT)
SELECT COUNT(*)
FROM meetings_meetingpage mp
JOIN meetings_meetingdocument md ON mp.document_id = md.id
WHERE mp.search_vector @@ websearch_to_tsquery('simple', 'police');
```

**What to look for:**
- Is the GIN index being used? (Should see `Bitmap Index Scan using meetingpage_search_vector_idx`)
- Is the bitmap scan "lossy"? (Means `work_mem` too low — bitmap overflows to disk)
- What's the actual time breakdown? (Is it the FTS filter, the JOIN, the ORDER BY, or the COUNT?)
- How many rows does the FTS match? (If "police" matches 2M of 12M rows, the count alone will be slow)

### Step 2: Check Table and Index Health (15 min)

```sql
-- Table size and bloat
SELECT
    pg_size_pretty(pg_total_relation_size('meetings_meetingpage')) as total_size,
    pg_size_pretty(pg_relation_size('meetings_meetingpage')) as table_size,
    pg_size_pretty(pg_indexes_size('meetings_meetingpage')) as indexes_size,
    n_live_tup,
    n_dead_tup,
    ROUND(100.0 * n_dead_tup / NULLIF(n_live_tup + n_dead_tup, 0), 2) as dead_pct,
    last_vacuum,
    last_autovacuum,
    last_analyze,
    last_autoanalyze
FROM pg_stat_user_tables
WHERE relname = 'meetings_meetingpage';

-- Index sizes and usage
SELECT
    indexrelname as index_name,
    pg_size_pretty(pg_relation_size(indexrelid)) as index_size,
    idx_scan as times_used,
    idx_tup_read,
    idx_tup_fetch
FROM pg_stat_user_indexes
WHERE relname = 'meetings_meetingpage'
ORDER BY pg_relation_size(indexrelid) DESC;

-- Check for pending GIN entries (should be 0 with fastupdate=off)
SELECT * FROM pg_stat_all_indexes
WHERE relname = 'meetings_meetingpage'
  AND indexrelname LIKE '%search_vector%';
```

**What to look for:**
- High dead tuple percentage (>5%) means vacuum isn't keeping up
- Very large index size relative to table size could indicate bloat
- Index not being scanned (idx_scan = 0) means the planner is choosing seq scan instead

### Step 3: Check PostgreSQL Configuration on Production (10 min)

```sql
-- Verify critical settings are actually applied
SHOW shared_buffers;
SHOW work_mem;
SHOW effective_cache_size;
SHOW jit;
SHOW random_page_cost;

-- Check if we're on CrunchyBridge or Docker in production
SELECT version();

-- Check current connections (are we saturated?)
SELECT count(*), state
FROM pg_stat_activity
GROUP BY state;
```

**Key question**: Is production actually using the tuned `postgresql.conf`, or is it on CrunchyBridge with defaults? The docker-compose config mounts a custom conf file, but if production uses CrunchyBridge, those settings may not be applied.

### Step 4: Identify the Real Bottleneck (20 min)

Based on Step 1-3 findings, the bottleneck is likely one of:

**A. The COUNT query is the killer.**
The `PostgresSearchBackend.search()` method calls `queryset.count()` (line 194 of `search_backends.py`) before pagination. For broad queries matching millions of rows, COUNT alone can take seconds.

**Fix**: Remove exact count. Use `EXPLAIN` row estimates, or cap count at a threshold:
```sql
-- Instead of exact COUNT(*)
SELECT
  CASE WHEN count > 10000 THEN 10000
       ELSE count
  END
FROM (SELECT COUNT(*) as count FROM ... LIMIT 10001) sub;
```
Or use the planner's estimate: `SELECT reltuples FROM pg_class WHERE relname = 'meetings_meetingpage'`

**B. Lossy bitmap heap scan.**
If `work_mem` is too low, the GIN bitmap spills to disk and rechecks every page.

**Fix**: Increase `work_mem` to 128MB or 256MB for search connections.

**C. ORDER BY on a different table's column.**
`ORDER BY md.meeting_date DESC` requires joining MeetingDocument and sorting — this can't use the GIN index.

**Fix**: Add `meeting_date` to MeetingPage (denormalized) with a composite index, or use keyset/cursor pagination to avoid full sort.

**D. Table bloat / stale statistics.**
If VACUUM hasn't run recently, the planner may have stale statistics and choose bad plans.

**Fix**: Run `VACUUM ANALYZE meetings_meetingpage;` and re-test.

**E. The Redis cache isn't working.**
If 5s on "hot" queries too, verify the cache is actually being read:

```python
# Quick test in Django shell
from searches.cache import get_cached_search_results

result = get_cached_search_results(
    search_term="police",
    municipalities=[],
    states=[],
    date_from=None,
    date_to=None,
    document_type="all",
    meeting_name_query="",
    limit=20,
    offset=0,
)
print(f"Cache hit: {result is not None}")
```

### Step 5: Apply Quick Fixes and Re-test (30 min)

Based on diagnosis, apply the most likely fixes:

1. **If COUNT is the bottleneck**: Replace exact count with capped count or estimate
2. **If lossy bitmap**: Increase `work_mem` to 256MB
3. **If ORDER BY**: Test query without the cross-table ORDER BY
4. **If bloat**: Run VACUUM ANALYZE
5. **If cache broken**: Fix cache configuration

Re-run the same EXPLAIN ANALYZE queries and compare times.

### Step 6: Decision Point (10 min)

After fixes, measure:
- If queries are now **< 500ms**: PostgreSQL FTS is viable. Document what was wrong and ship the fix.
- If queries are **500ms - 2s**: Marginal. PostgreSQL works for now but plan Quickwit migration for 100M scale.
- If queries are still **> 2s**: PostgreSQL FTS isn't the right tool at this data volume. Proceed to Quickwit implementation.

---

## Most Likely Culprit

Based on the code review, **my top suspect is the `COUNT(*)` query** on line 194 of `search_backends.py`. For a broad search term like "police" or "budget" that matches hundreds of thousands of pages across 12M rows, the COUNT alone requires scanning the full bitmap result — this is O(matches), not O(limit). Combined with the cross-table JOIN for the ORDER BY, you get a query that touches far more data than the 20 results displayed.

The Redis cache *should* help on repeat queries, but if the cache key normalization or serialization has any issue, every query hits the database.

---

## Files to Investigate

| File | Why |
|------|-----|
| `searches/search_backends.py:194` | The `count()` call — likely bottleneck |
| `searches/cache.py` | Verify cache is actually working |
| `searches/services.py` | The `_apply_search_filters` and `_apply_meeting_name_filter` functions |
| `config/settings/production.py` | What DB settings are actually used in prod |
| `docker/postgres/postgresql.conf` | Local dev PG config (already reviewed) |
| `docs/crunchybridge-configuration.md` | Production PG config guidance |
