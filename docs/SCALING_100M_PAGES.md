# PostgreSQL Scaling Plan for 100M+ Pages

**Date**: 2025-11-19
**Context**: Planning for scaling civic-observer to handle 100+ million pages of meeting minutes with full-text search

---

## Current State Analysis

### Critical Bottlenecks Identified

**Immediate Showstoppers**:
1. **Pagination**: OFFSET/LIMIT will fail beyond ~100k pages
2. **No Partitioning**: Single table with 100M+ rows
3. **No Connection Pooling**: Will hit max_connections under load
4. **Default PostgreSQL Memory**: Configured for <1GB data, not 100M+ rows
5. **No Shared Memory**: Docker container will OOM on large operations
6. **Auto-Update Triggers**: Every page INSERT runs expensive text processing
7. **Storage Duplication**: search_vector duplicates text content
8. **No Archival Strategy**: Old meeting data grows unbounded

**Performance Degradation Points**:
1. Deep pagination becomes unusable
2. Full-text search sorts entire result set
3. Headline generation creates N queries per page
4. No query result caching
5. Index maintenance on 100M row table
6. VACUUM operations on giant table

**Resource Exhaustion Risks**:
1. Connection pool exhaustion (no pooling)
2. Memory exhaustion (no limits, wrong settings)
3. Disk space (no compression, duplicated data)
4. CPU from trigger overhead during bulk ingestion

---

## PHASE 1: Critical Infrastructure (Do First) 🔴

**Priority**: Highest - Prevents immediate failures
**Timeline**: Week 1
**Risk if skipped**: Application will fail under production load

### 1.1 PostgreSQL Configuration Tuning

**Objective**: Configure PostgreSQL for large-scale operations

**Implementation**:
- **File**: Create `docker/postgresql.conf`
- **Changes**:
  ```conf
  # Memory Configuration
  shared_buffers = 4GB                    # 25% of dedicated RAM (assumes 16GB server)
  effective_cache_size = 12GB             # 75% of RAM for query planning
  work_mem = 64MB                         # Per-operation memory for sorts/hashes
  maintenance_work_mem = 1GB              # For index creation, VACUUM, etc.

  # Connection Configuration
  max_connections = 200                   # Higher limit (with pgBouncer pooling)

  # Write-Ahead Log (WAL) Configuration
  wal_buffers = 16MB
  checkpoint_completion_target = 0.9      # Spread out checkpoint I/O

  # Query Planner Configuration
  random_page_cost = 1.1                  # For SSDs (default 4.0 is for HDDs)
  effective_io_concurrency = 200          # For SSDs with parallel I/O

  # Autovacuum Tuning (critical for large tables)
  autovacuum_max_workers = 4
  autovacuum_naptime = 10s                # More frequent autovacuum
  autovacuum_vacuum_scale_factor = 0.05   # Trigger VACUUM at 5% changes
  autovacuum_analyze_scale_factor = 0.02  # Trigger ANALYZE at 2% changes
  ```

**Expected Impact**:
- Query performance: 2-5x faster
- Index creation: 10x faster
- Prevents out-of-memory errors

---

### 1.2 Docker Container Resources

**Objective**: Properly resource Docker PostgreSQL container

**Implementation**:
- **File**: `docker-compose.yml`
- **Changes**:
  ```yaml
  db:
    image: postgres:17-alpine
    shm_size: 2gb                        # Shared memory for sorts/hashes
    deploy:
      resources:
        limits:
          cpus: '4.0'
          memory: 16G
        reservations:
          cpus: '2.0'
          memory: 8G
    volumes:
      - postgres-data:/var/lib/postgresql/data/
      - ./docker/postgresql.conf:/etc/postgresql/postgresql.conf
    command: postgres -c config_file=/etc/postgresql/postgresql.conf
    environment:
      POSTGRES_HOST_AUTH_METHOD: trust
    ports:
      - "5433:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready"]
      interval: 10s
      timeout: 5s
      retries: 3
  ```

**Expected Impact**:
- Prevents container OOM kills
- Ensures PostgreSQL has resources it needs
- Enables large operations (index creation, VACUUM)

---

### 1.3 Connection Pooling with PgBouncer

**Objective**: Add connection pooling layer to prevent connection exhaustion

**Implementation**:
- **Files**:
  - Add to `docker-compose.yml`
  - Create `docker/pgbouncer.ini`

**docker-compose.yml addition**:
```yaml
pgbouncer:
  image: pgbouncer/pgbouncer:latest
  environment:
    DATABASES_HOST: db
    DATABASES_PORT: 5432
    DATABASES_USER: postgres
    DATABASES_DBNAME: postgres
    PGBOUNCER_POOL_MODE: transaction
    PGBOUNCER_MAX_CLIENT_CONN: 200
    PGBOUNCER_DEFAULT_POOL_SIZE: 25
    PGBOUNCER_RESERVE_POOL_SIZE: 5
  ports:
    - "6432:6432"
  depends_on:
    - db
```

**Application Changes**:
- Update `DATABASE_URL` to point to pgbouncer: `postgres://postgres@pgbouncer:6432/postgres`

**Expected Impact**:
- Support 200+ concurrent users vs 20-30 without pooling
- Reduced connection overhead
- Better resource utilization

---

## PHASE 2: Database Schema Optimization 🟡

**Priority**: High - Enables scaling to 100M rows
**Timeline**: Week 2
**Risk if skipped**: Queries will timeout, pagination will fail

### 2.1 Table Partitioning Strategy

**Objective**: Partition MeetingPage table to enable scaling to 100M+ rows

**Status**: ⚠️ **Requires Django-specific considerations** - see detailed analysis below

**Critical Findings**:
- Django has **no native support** for PostgreSQL partitioning
- Cannot partition by `document__meeting_date` directly (not a column on MeetingPage)
- Requires composite primary key: `PRIMARY KEY (id, partition_key)`
- Must use raw SQL migrations or third-party package
- Queries work **transparently** once implemented - no ORM changes needed

**Three Partitioning Options Analyzed**:

1. **Option A: Denormalize `meeting_date` to MeetingPage** ⭐ **RECOMMENDED**
   - Add `meeting_date` field directly to MeetingPage (copy from document)
   - Partition by RANGE on `meeting_date`
   - Best performance for date-filtered queries
   - Enables archival by meeting date
   - 400MB storage overhead for 100M rows (negligible)

2. **Option B: Partition by `created` timestamp**
   - Use existing `created` field from TimeStampedModel
   - No schema changes needed
   - Poor partition pruning (queries filter by meeting_date, not created)
   - Only useful if backfill is not a concern

3. **Option C: No partitioning, focus on indexes**
   - Simplest approach
   - Slower maintenance (VACUUM on 100M rows)
   - No archival strategy
   - May not scale beyond 100M rows

**Detailed Implementation Guide**:

See **[PAGE_PARTITIONING.md](PAGE_PARTITIONING.md)** for:
- Comprehensive Django ORM compatibility analysis
- Complete migration code examples
- Partition management commands
- Query patterns and performance expectations
- Testing strategy and monitoring
- Rollback procedures

**Recommended Implementation** (Option A):
1. Add `meeting_date` field to MeetingPage (denormalized from document)
2. Create partitioned table with raw SQL migration
3. Use composite primary key: `PRIMARY KEY (id, meeting_date)`
4. Set `managed=False` on Django model
5. Create monthly partitions via management command

**Expected Impact**:
- Query time with date filter: **10-20x faster** (partition pruning)
- Query time without date filter: **2-3x faster** (parallel scanning, smaller indexes)
- Maintenance: VACUUM/ANALYZE **50-100x faster** (per partition vs. full table)
- Archival: Easy to detach partitions older than 2 years

---

### 2.2 Index Optimization

**Objective**: Optimize indexes for partitioned table and common queries

**Implementation**:
- **File**: New migration `meetings/migrations/0006_optimize_indexes.py`

**Changes**:

1. **Covering Index for Search Results**:
   ```sql
   CREATE INDEX CONCURRENTLY meetingpage_search_covering_idx
   ON meetings_meetingpage (document_id, created DESC)
   INCLUDE (id, page_number, text);
   ```
   - Allows index-only scans (no table access needed)
   - Dramatically faster for pagination

2. **Partial Indexes for Active Data**:
   ```sql
   -- Index only recent data (last 2 years)
   CREATE INDEX CONCURRENTLY meetingpage_recent_search_idx
   ON meetings_meetingpage USING GIN (search_vector)
   WHERE created > NOW() - INTERVAL '2 years';
   ```
   - Smaller index = faster searches on recent data
   - Most searches target recent meetings

3. **Adjust FILLFACTOR**:
   ```sql
   ALTER TABLE meetings_meetingpage SET (fillfactor = 90);
   ```
   - Leaves 10% free space for HOT updates
   - Reduces table bloat on frequently updated rows

4. **Increase Statistics Targets**:
   ```sql
   ALTER TABLE meetings_meetingpage
   ALTER COLUMN search_vector SET STATISTICS 1000;
   ALTER TABLE meetings_meetingpage
   ALTER COLUMN document_id SET STATISTICS 1000;
   ```
   - Better query planning for complex queries
   - More accurate cost estimates

**Expected Impact**:
- Search queries: 2-3x faster
- Reduced table bloat
- Better query plans

---

### 2.3 Cursor-Based Pagination

**Objective**: Replace OFFSET/LIMIT pagination with cursor-based pagination

**Rationale**:
- OFFSET 10000 LIMIT 20 requires scanning 10,020 rows
- At 100M rows, deep pagination is O(n) - becomes unusable
- Cursor pagination is O(1) - constant time regardless of page depth

**Implementation**:
- **File**: `meetings/views.py`

**Current Code**:
```python
# Uses Django Paginator (OFFSET/LIMIT)
paginator = Paginator(queryset, SEARCH_RESULTS_PER_PAGE)
page_obj = paginator.get_page(page_number)
```

**New Code**:
```python
# Cursor-based pagination
def get_paginated_results(queryset, cursor=None, page_size=20):
    """
    Cursor-based pagination using (created, id) as cursor.

    Cursor format: base64(created_timestamp:id)
    """
    if cursor:
        # Decode cursor
        created, pk = decode_cursor(cursor)
        queryset = queryset.filter(
            Q(created__lt=created) | Q(created=created, id__lt=pk)
        )

    # Get page_size + 1 to check if there's a next page
    results = list(queryset.order_by("-created", "-id")[: page_size + 1])
    has_next = len(results) > page_size

    if has_next:
        results = results[:-1]
        next_cursor = encode_cursor(results[-1].created, results[-1].id)
    else:
        next_cursor = None

    return {
        "results": results,
        "next_cursor": next_cursor,
        "has_next": has_next,
    }
```

**Template Changes**:
- Replace page numbers with "Next" / "Previous" buttons
- Use cursor in URL: `?cursor=eyJjcmVhdGVkIjoxNjM...`

**Expected Impact**:
- Deep pagination: Constant time (no performance degradation)
- Required for any pagination beyond ~100k results
- **Tradeoff**: Can't jump to arbitrary page numbers (acceptable for search UX)

---

## PHASE 3: Application Optimizations 🟢

**Priority**: Medium - Improves performance and reduces load
**Timeline**: Week 3
**Risk if skipped**: Higher costs, slower responses, but app still functions

### 3.1 Query Optimizations

**Objective**: Reduce data transfer and query count

**Implementation**:
- **File**: `meetings/views.py`

**Changes**:

1. **Reduce Column Fetching**:
   ```python
   # Before: Fetches all columns including full text
   queryset = MeetingPage.objects.select_related(...)

   # After: Only fetch needed columns
   queryset = MeetingPage.objects.select_related(...).only(
       "id",
       "page_number",
       "created",
       "document__id",
       "document__meeting_name",
       "document__meeting_date",
       "document__document_type",
       "document__municipality__name",
   )
   # Fetch full text only when needed (for headline generation)
   ```

2. **Batch Headline Generation**:
   ```python
   # Before: N+1 queries for headlines
   for result in page_results:
       result.headline = generate_headline(result.id, query)

   # After: Single query with SearchHeadline
   from django.contrib.postgres.search import SearchHeadline

   page_ids = [r.id for r in page_results]
   headlines = (
       MeetingPage.objects.filter(pk__in=page_ids)
       .annotate(
           headline=SearchHeadline(
               "text",
               search_query,
               config="simple",
               start_sel="<mark>",
               stop_sel="</mark>",
           )
       )
       .in_bulk(field_name="id")
   )

   for result in page_results:
       result.headline = headlines[result.id].headline
   ```

3. **Optimize Meeting Name Filter**:
   ```python
   # Before: Subquery to get document IDs
   matching_doc_ids = MeetingDocument.objects.filter(...).values_list("id", flat=True)
   queryset = queryset.filter(document_id__in=matching_doc_ids)

   # After: Use JOIN instead of IN clause
   queryset = queryset.filter(
       document__meeting_name_search_vector=meeting_name_search_query
   )
   ```

**Expected Impact**:
- Data transfer: 50-70% reduction
- Query count: Reduced from N+21 to 2 queries per page
- Response time: 30-40% faster

---

### 3.2 Search Result Caching

**Objective**: Cache common search queries to reduce database load

**Implementation**:
- **Files**:
  - Create `meetings/cache.py`
  - Update `meetings/views.py`

**Strategy**:
- Cache search results in Redis
- Cache key: hash(query + filters + cursor)
- TTL: 60 seconds (balance freshness vs. cache hit rate)
- Only cache first 3 pages of results

**Code**:
```python
from django.core.cache import cache
import hashlib
import json


def get_cached_search_results(query, filters, cursor=None):
    """Get cached search results or None if not cached."""
    cache_key = generate_cache_key(query, filters, cursor)
    return cache.get(cache_key)


def cache_search_results(query, filters, cursor, results, ttl=60):
    """Cache search results for TTL seconds."""
    cache_key = generate_cache_key(query, filters, cursor)
    cache.set(cache_key, results, ttl)


def generate_cache_key(query, filters, cursor):
    """Generate deterministic cache key from search parameters."""
    data = {
        "query": query,
        "filters": filters,
        "cursor": cursor,
    }
    hash_input = json.dumps(data, sort_keys=True)
    return f"search:{hashlib.md5(hash_input.encode()).hexdigest()}"
```

**Expected Impact**:
- Cache hit rate: 40-60% (common searches, repeated queries)
- Database load: 40-60% reduction
- Response time for cached queries: <50ms

---

### 3.3 Bulk Data Ingestion Optimization

**Objective**: Speed up initial data ingestion and backfill operations

**Implementation**:
- **File**: `meetings/services.py`

**Changes**:

1. **Use bulk_create Instead of update_or_create**:
   ```python
   # Before: O(n) individual updates
   for page_data in pages:
       MeetingPage.objects.update_or_create(id=page_data["id"], defaults={...})

   # After: O(1) bulk operation
   pages_to_create = []
   pages_to_update = []

   existing_ids = set(
       MeetingPage.objects.filter(id__in=[p["id"] for p in pages]).values_list(
           "id", flat=True
       )
   )

   for page_data in pages:
       page = MeetingPage(id=page_data["id"], ...)
       if page_data["id"] in existing_ids:
           pages_to_update.append(page)
       else:
           pages_to_create.append(page)

   MeetingPage.objects.bulk_create(pages_to_create, ignore_conflicts=True)
   MeetingPage.objects.bulk_update(pages_to_update, fields=[...])
   ```

2. **Disable Triggers During Bulk Import**:
   ```python
   # For initial bulk load, disable search_vector trigger
   with connection.cursor() as cursor:
       cursor.execute("ALTER TABLE meetings_meetingpage DISABLE TRIGGER ALL;")
       # Bulk insert
       MeetingPage.objects.bulk_create(pages, batch_size=1000)
       cursor.execute("ALTER TABLE meetings_meetingpage ENABLE TRIGGER ALL;")
       # Update search_vectors in batch
       cursor.execute(
           """
           UPDATE meetings_meetingpage
           SET search_vector = to_tsvector('simple', unaccent(coalesce(text, '')))
           WHERE search_vector IS NULL
       """
       )
   ```

**Expected Impact**:
- Ingestion speed: 50-100x faster
- Initial backfill: Hours instead of days
- Reduced trigger overhead during bulk operations

---

### 3.4 Database Connection Settings

**Objective**: Optimize Django database connection behavior

**Implementation**:
- **File**: `config/settings/base.py`

**Changes**:
```python
DATABASES = {
    "default": {
        **env.dj_db_url("DATABASE_URL"),
        "CONN_MAX_AGE": 600,  # Keep connections alive for 10 minutes
        "OPTIONS": {
            "connect_timeout": 10,
            "options": "-c statement_timeout=30000",  # 30 second query timeout
            "keepalives": 1,
            "keepalives_idle": 30,
            "keepalives_interval": 10,
            "keepalives_count": 5,
        },
        "ATOMIC_REQUESTS": False,  # Don't wrap every view in transaction
    }
}
```

**Expected Impact**:
- Reduced connection overhead
- Prevents runaway queries
- Better connection reliability

---

## PHASE 4: Monitoring & Observability 📊

**Priority**: Medium - Required for ongoing optimization
**Timeline**: Week 4
**Risk if skipped**: Can't identify bottlenecks, no alerting on issues

### 4.1 Enable PostgreSQL Monitoring Extensions

**Objective**: Enable query performance tracking

**Implementation**:
- **File**: New migration `meetings/migrations/0007_enable_monitoring.py`

**Changes**:
```sql
-- Enable pg_stat_statements for query performance tracking
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

-- Configure auto_explain for slow queries
ALTER SYSTEM SET auto_explain.log_min_duration = 1000;  -- Log queries >1 second
ALTER SYSTEM SET auto_explain.log_analyze = true;
ALTER SYSTEM SET auto_explain.log_buffers = true;
ALTER SYSTEM SET auto_explain.log_timing = true;
ALTER SYSTEM SET auto_explain.log_verbose = true;

-- Enable statement logging for slow queries
ALTER SYSTEM SET log_min_duration_statement = 1000;  -- Log slow queries
ALTER SYSTEM SET log_line_prefix = '%t [%p]: user=%u,db=%d,app=%a,client=%h ';
ALTER SYSTEM SET log_checkpoints = on;
ALTER SYSTEM SET log_connections = on;
ALTER SYSTEM SET log_disconnections = on;
ALTER SYSTEM SET log_lock_waits = on;

SELECT pg_reload_conf();
```

**Expected Impact**:
- Visibility into slow queries
- Ability to identify optimization opportunities
- Query plan analysis for debugging

---

### 4.2 Application-Level Query Logging

**Objective**: Log slow Django ORM queries

**Implementation**:
- **File**: `config/settings/base.py`

**Changes**:
```python
# Development: Log all queries
if DEBUG:
    LOGGING["loggers"]["django.db.backends"] = {
        "level": "DEBUG",
        "handlers": ["console"],
    }

# Production: Log slow queries only
else:
    LOGGING["loggers"]["django.db.backends"] = {
        "level": "WARNING",
        "handlers": ["console"],
        "filters": ["require_debug_false"],
    }
```

**Custom Middleware** (`monitoring/middleware.py`):
```python
import time
from django.db import connection


class QueryCountDebugMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Reset query count
        queries_before = len(connection.queries)

        start_time = time.time()
        response = self.get_response(request)
        duration = time.time() - start_time

        queries_after = len(connection.queries)
        query_count = queries_after - queries_before

        if duration > 1.0 or query_count > 10:
            logger.warning(
                f"Slow request: {request.path} took {duration:.2f}s "
                f"with {query_count} queries"
            )

        return response
```

**Expected Impact**:
- Identify N+1 query problems
- Track query performance over time
- Alert on performance regressions

---

### 4.3 Monitoring Dashboard

**Objective**: Visualize database performance metrics

**Implementation**:
- **File**: `docker-compose.yml` (add monitoring services)

**Option A: pgAdmin** (Simple, PostgreSQL-focused):
```yaml
pgadmin:
  image: dpage/pgadmin4:latest
  environment:
    PGADMIN_DEFAULT_EMAIL: admin@civic-observer.local
    PGADMIN_DEFAULT_PASSWORD: admin
  ports:
    - "5050:80"
  depends_on:
    - db
```

**Option B: Grafana + Prometheus + postgres_exporter** (Production-grade):
```yaml
prometheus:
  image: prom/prometheus:latest
  volumes:
    - ./docker/prometheus.yml:/etc/prometheus/prometheus.yml
    - prometheus-data:/prometheus
  ports:
    - "9090:9090"

postgres-exporter:
  image: prometheuscommunity/postgres-exporter:latest
  environment:
    DATA_SOURCE_NAME: "postgresql://postgres@db:5432/postgres?sslmode=disable"
  depends_on:
    - db

grafana:
  image: grafana/grafana:latest
  ports:
    - "3000:3000"
  volumes:
    - grafana-data:/var/lib/grafana
    - ./docker/grafana-dashboards:/etc/grafana/provisioning/dashboards
  depends_on:
    - prometheus
```

**Key Metrics to Monitor**:
- Query response time (p50, p95, p99)
- Queries per second
- Connection pool usage
- Cache hit rate
- Table and index sizes
- Replication lag (if using replicas)
- Disk I/O and space usage

**Expected Impact**:
- Real-time visibility into database health
- Proactive alerting on issues
- Historical trend analysis

---

## PHASE 5: Advanced Optimizations (Later) 🔵

**Priority**: Low - Nice to have, implement as needed
**Timeline**: Months 2-6
**Risk if skipped**: Higher costs, but not critical

### 5.1 Read Replicas

**Objective**: Scale read operations horizontally

**Implementation**:
- Configure PostgreSQL streaming replication
- Add read replica in `docker-compose.yml`
- Route search queries to replica, writes to primary

**Django Database Router**:
```python
class PrimaryReplicaRouter:
    def db_for_read(self, model, **hints):
        if model._meta.app_label == "meetings":
            return "replica"
        return "default"

    def db_for_write(self, model, **hints):
        return "default"
```

**Expected Impact**:
- 2x read capacity
- Reduced load on primary
- Better write performance

---

### 5.2 Compression & Storage Optimization

**Objective**: Reduce storage costs and improve I/O

**Implementation**:

1. **TOAST Compression**:
   ```sql
   -- Already enabled by default for text columns
   -- Can tune compression algorithm
   ALTER TABLE meetings_meetingpage
   ALTER COLUMN text SET STORAGE EXTENDED;
   ```

2. **pg_repack for Table Bloat**:
   ```bash
   # Run monthly to reclaim space
   pg_repack -t meetings_meetingpage
   ```

3. **Archive Old Partitions**:
   ```python
   # Management command to archive partitions older than 2 years
   # Detach partition → dump to S3 → drop partition
   ```

**Expected Impact**:
- Storage: 30-40% reduction
- I/O: 20-30% faster (less data to read)
- Cost: Significant savings on storage

---

### 5.3 Materialized Views

**Objective**: Pre-compute expensive aggregations

**Implementation**:
```sql
-- Example: Popular search terms
CREATE MATERIALIZED VIEW popular_searches AS
SELECT
    query,
    COUNT(*) as search_count,
    MAX(created) as last_searched
FROM search_logs
GROUP BY query
ORDER BY search_count DESC
LIMIT 1000;

-- Refresh nightly
REFRESH MATERIALIZED VIEW CONCURRENTLY popular_searches;
```

**Expected Impact**:
- Fast access to pre-computed data
- Reduced load on main tables
- Better user experience (trending searches, etc.)

---

## Performance Benchmarks

### Before Optimizations
| Metric | Value |
|--------|-------|
| Search on 100M rows | 10-30 seconds |
| Deep pagination (page 1000) | Timeout |
| Concurrent users supported | 20-30 |
| Index creation time | Hours |
| Bulk import (1M rows) | 2-4 hours |
| Cache hit rate | 0% |

### After Phase 1-3 Optimizations
| Metric | Value |
|--------|-------|
| Search on partitioned data | 0.5-2 seconds |
| Deep pagination (cursor-based) | <200ms |
| Concurrent users supported | 200+ |
| Index creation time | Minutes |
| Bulk import (1M rows) | 2-5 minutes |
| Cache hit rate | 40-60% |

### Performance Improvements
| Operation | Improvement |
|-----------|-------------|
| Search queries | **10-20x faster** |
| Pagination | **50-100x faster** |
| Bulk import | **50-100x faster** |
| Concurrent capacity | **10x increase** |
| Cached queries | **100x faster** |

---

## Implementation Checklist

### Phase 1: Critical Infrastructure
- [ ] Create `docker/postgresql.conf` with tuned settings
- [ ] Update `docker-compose.yml` with resource limits and shared memory
- [ ] Add pgBouncer container and configuration
- [ ] Update `DATABASE_URL` to point to pgBouncer
- [ ] Test connection pooling works correctly
- [ ] Update `config/settings/base.py` with connection settings

### Phase 2: Database Schema
- [ ] Review [PAGE_PARTITIONING.md](PAGE_PARTITIONING.md) for detailed partitioning implementation
- [ ] Add `meeting_date` field to MeetingPage model (denormalized from document)
- [ ] Create raw SQL migration for table partitioning (see PAGE_PARTITIONING.md)
- [ ] Test partition creation on dev database
- [ ] Create `create_meetingpage_partitions` management command
- [ ] Schedule monthly partition creation (cron)
- [ ] Set `managed=False` on MeetingPage model
- [ ] Create migration for index optimization
- [ ] Implement cursor-based pagination in views
- [ ] Update templates for cursor pagination UI
- [ ] Test pagination with large datasets

### Phase 3: Application Optimizations
- [ ] Implement `.only()` for column filtering
- [ ] Batch headline generation
- [ ] Optimize meeting name filter query
- [ ] Implement search result caching
- [ ] Optimize bulk ingestion with `bulk_create`
- [ ] Add query logging middleware
- [ ] Test caching hit rates

### Phase 4: Monitoring
- [ ] Create migration to enable pg_stat_statements
- [ ] Configure auto_explain for slow queries
- [ ] Set up pgAdmin or Grafana dashboard
- [ ] Configure alerting for slow queries
- [ ] Set up disk space monitoring
- [ ] Create runbook for common issues

### Phase 5: Advanced (Optional)
- [ ] Configure PostgreSQL replication
- [ ] Implement database router for read/write splitting
- [ ] Set up pg_repack for monthly maintenance
- [ ] Create archival process for old partitions
- [ ] Implement materialized views if needed

---

## Testing Strategy

### Load Testing
1. **Generate test data**: 10M, 50M, 100M rows
2. **Benchmark queries**:
   - Simple search: 10,000 queries
   - Complex search with filters: 1,000 queries
   - Pagination (various depths): 1,000 queries
3. **Concurrent load**: 50, 100, 200 concurrent users
4. **Bulk import**: 1M rows

### Performance Regression Testing
- Add performance tests to CI/CD
- Alert on >20% performance degradation
- Track query times over time

### Monitoring Validation
- Verify metrics are being collected
- Test alerting triggers correctly
- Ensure dashboards show accurate data

---

## Rollback Plan

### Phase 1 (Configuration)
- Restore old `docker-compose.yml`
- Restart containers
- **Risk**: Low (configuration only)

### Phase 2 (Partitioning)
- Keep old table during migration
- Test new partitioned table thoroughly
- Swap back if issues found
- **Risk**: Medium (requires downtime)

### Phase 3 (Application)
- Feature flags for new code
- Gradual rollout (10% → 50% → 100%)
- Monitor error rates
- **Risk**: Low (code changes)

---

## Cost Implications

### Infrastructure Costs
- **PostgreSQL server**: Needs 16GB RAM, 4 CPU → ~$100-200/month
- **pgBouncer**: Minimal overhead → Included
- **Redis**: For caching → ~$20-50/month
- **Monitoring**: pgAdmin free, Grafana free → $0

### Storage Costs
- **100M pages × 2KB avg text**: ~200GB raw data
- **With indexes and search_vector**: ~400-500GB total
- **With partitioning and compression**: ~300-400GB
- **Cost at $0.10/GB/month**: ~$30-50/month

### Performance Benefits
- **Reduced query time**: 10-20x faster
- **Higher throughput**: Support 10x more users
- **Better UX**: Sub-second search results
- **ROI**: Positive (faster development, happier users)

---

## Conclusion

This scaling plan provides a **comprehensive, phased approach** to scaling civic-observer from thousands to 100+ million pages while maintaining fast search performance.

**Key Takeaways**:
1. **Phase 1 is critical** - Configuration changes prevent immediate failures
2. **Partitioning is non-negotiable** - Required for 100M+ row tables
3. **Cursor pagination is required** - OFFSET/LIMIT won't work at scale
4. **Monitoring is essential** - Can't optimize what you can't measure
5. **Test at scale** - Load testing with realistic data volumes

**Recommended Start**: Implement Phase 1 immediately, then proceed with Phase 2 before data grows to >10M rows. Phases 3-5 can be implemented incrementally based on actual performance needs.

---

**Questions or clarifications needed?** This is a living document - update as implementation progresses and lessons are learned.
