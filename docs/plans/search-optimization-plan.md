# Search Optimization Plan for Civic Observer

**Status**: Phase 1 In Progress
**Created**: 2026-02-06
**Last Updated**: 2026-02-06

## Executive Summary

This document outlines a comprehensive, phased approach to optimizing PostgreSQL full-text search for civic-observer's 12M+ document corpus. The goal is to achieve consistent sub-100ms query times for 95% of searches while maintaining the current PostgreSQL-based architecture.

**Current State**: Search is already ~50× faster than baseline thanks to:
- Pre-computed `search_vector` columns with triggers (migrations 0003 & 0004)
- Removed ts_rank computation, sorting by date instead (commits b4b380c, d50d9be)
- Reverted progressive loading complexity (commit 42570f6)

**Remaining Issues**:
- GIN indexes using default `fastupdate=True` causing latency spikes
- PostgreSQL configuration not optimized for FTS workloads
- No caching layer for repeated queries
- Edge cases with very broad queries may still be slow

---

## Research Background

This plan is based on comprehensive research into PostgreSQL FTS optimization at scale. Key findings:

1. **Stored tsvector columns**: Pre-computing tsvector eliminates 50× overhead vs on-the-fly computation
2. **fastupdate=False on GIN indexes**: Prevents 465ms-3,155ms latency spikes from pending list cleanup (GitLab case study)
3. **Remove ts_rank from hot path**: Computing rank for millions of rows is expensive; use date sorting or capped sampling
4. **PostgreSQL config tuning**: Default settings (JIT on, work_mem=4MB, random_page_cost=4.0) are hostile to FTS
5. **Caching**: Power-law query distribution means 80-90% of searches can be served from cache

**Benchmark Context**: Xata.io testing showed:
- Narrow queries (2 matches): ~1ms
- Broad queries (1M+ matches): 25+ seconds with ranking
- Solution: Capped sampling (rank only first 5k results) reduces to <50ms

---

## Phase 1: Quick Wins (1-2 hours)

**Goal**: Eliminate latency spikes and reduce query times 2-5× with minimal code changes.
**Expected Outcome**: Consistent sub-100ms queries for 95%+ of searches.

### 1.1 Disable fastupdate on GIN Indexes

**Problem**: GIN indexes with `fastupdate=True` (default) accumulate a "pending list" of new entries that must be scanned on every query and periodically merged, causing unpredictable spikes.

**Solution**: Rebuild GIN indexes with `fastupdate=False`.

**Implementation**:
```python
# meetings/migrations/000X_optimize_gin_indexes.py
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("meetings", "0007_add_composite_index_for_search"),
    ]

    operations = [
        # Drop existing indexes
        migrations.RunSQL(
            sql="""
                DROP INDEX IF EXISTS meetingpage_search_vector_idx;
                DROP INDEX IF EXISTS meetingdocument_meeting_name_search_idx;
            """,
            reverse_sql="""
                CREATE INDEX meetingpage_search_vector_idx
                ON meetings_meetingpage USING GIN (search_vector);
                CREATE INDEX meetingdocument_meeting_name_search_idx
                ON meetings_meetingdocument USING GIN (meeting_name_search_vector);
            """,
        ),
        # Recreate with fastupdate=False and optimized settings
        migrations.RunSQL(
            sql="""
                CREATE INDEX meetingpage_search_vector_idx
                ON meetings_meetingpage
                USING GIN (search_vector)
                WITH (fastupdate=off);

                CREATE INDEX meetingdocument_meeting_name_search_idx
                ON meetings_meetingdocument
                USING GIN (meeting_name_search_vector)
                WITH (fastupdate=off);
            """,
            reverse_sql="""
                DROP INDEX IF EXISTS meetingpage_search_vector_idx;
                DROP INDEX IF EXISTS meetingdocument_meeting_name_search_idx;
            """,
        ),
    ]
```

**Notes**:
- This is a DDL operation but uses `CREATE INDEX CONCURRENTLY` safety via separate migration
- For production: Consider running manually with `CONCURRENTLY` to avoid locking
- Index rebuild will take ~15-30 minutes on 12M rows with `maintenance_work_mem=2GB`

**Files to modify**:
- Create new migration: `meetings/migrations/000X_optimize_gin_indexes.py`

---

### 1.2 PostgreSQL Configuration Tuning

**Problem**: Default PostgreSQL settings are optimized for general OLTP workloads, not FTS-heavy read workloads.

**Solution**: Add FTS-optimized configuration to docker-compose and production environment.

**Key Settings** (sized for 64GB RAM server, scale proportionally):

```ini
# Memory - the biggest levers
shared_buffers = 16GB              # 25% of RAM (default 128MB is too low)
work_mem = 64MB                    # per-sort operation; prevents lossy bitmap scans
maintenance_work_mem = 2GB         # speeds GIN index builds/REINDEX
effective_cache_size = 48GB        # 75% of RAM; tells planner to prefer index scans

# I/O - critical for SSD
random_page_cost = 1.1             # default 4.0 assumes HDD; SSD needs 1.1
effective_io_concurrency = 200     # enables prefetching during bitmap heap scans

# JIT - DISABLE for FTS/OLTP
jit = off                          # JIT compilation adds 50-200ms overhead per query
                                   # Adam Johnson documented 3.5s → 10ms improvement

# Autovacuum - aggressive for high-churn table
# (Set per-table in migration, not globally)
```

**Implementation**:

**Note**: Production uses CrunchyBridge (managed PostgreSQL), so configuration differs:
- **Development**: Configure via docker-compose.yml and postgresql.conf
- **Production**: Configure via CrunchyBridge console/CLI (see docs/crunchybridge-configuration.md)

**Docker Development** (`docker-compose.yml`):
```yaml
services:
  db:
    image: postgres:17-alpine
    environment:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
      POSTGRES_DB: postgres
    ports:
      - "5433:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./docker/postgres/postgresql.conf:/etc/postgresql/postgresql.conf
    command: postgres -c config_file=/etc/postgresql/postgresql.conf
```

Create `docker/postgres/postgresql.conf`:
```ini
# Civic Observer PostgreSQL Configuration - FTS Optimized
# For 8GB Docker allocation (adjust for production 64GB server)

# Memory Configuration
shared_buffers = 2GB                    # 25% of allocated RAM
work_mem = 64MB                         # Per-operation sort buffer
maintenance_work_mem = 512MB            # For index builds/vacuum
effective_cache_size = 6GB              # 75% of allocated RAM

# SSD Optimization
random_page_cost = 1.1                  # SSD vs HDD (default 4.0)
effective_io_concurrency = 200          # Parallel I/O requests

# Performance
jit = off                               # Disable JIT for OLTP/FTS workloads

# Connection Settings (for PgBouncer compatibility)
max_connections = 100
```

**Production (CrunchyBridge)**:

Per-connection settings in `config/settings/production.py` (already applied):
```python
# config/settings/production.py
DATABASES: dict[str, dict[str, Any]] = {
    "default": {
        **env.dj_db_url("DATABASE_URL"),
        "CONN_MAX_AGE": 600,  # Keep connections alive
        "CONN_HEALTH_CHECKS": True,
        "OPTIONS": {
            # FTS optimization: Disable JIT, increase work_mem
            "options": "-c jit=off -c work_mem=64MB"
        },
    }
}
```

Cluster-wide settings via CrunchyBridge console/CLI:
```bash
# See docs/crunchybridge-configuration.md for complete guide
cb config set <cluster-id> jit off
cb config set <cluster-id> random_page_cost 1.1
cb config set <cluster-id> effective_io_concurrency 200
cb config set <cluster-id> work_mem '64MB'
cb config set <cluster-id> maintenance_work_mem '512MB'
```

**Per-Table Autovacuum Tuning** (migration):
```python
# meetings/migrations/000X_tune_autovacuum.py
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("meetings", "000X_optimize_gin_indexes"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
                ALTER TABLE meetings_meetingpage SET (
                    autovacuum_vacuum_scale_factor = 0.02,
                    autovacuum_analyze_scale_factor = 0.01,
                    autovacuum_vacuum_cost_limit = 2000,
                    autovacuum_vacuum_cost_delay = 2
                );
            """,
            reverse_sql="""
                ALTER TABLE meetings_meetingpage RESET (
                    autovacuum_vacuum_scale_factor,
                    autovacuum_analyze_scale_factor,
                    autovacuum_vacuum_cost_limit,
                    autovacuum_vacuum_cost_delay
                );
            """,
        ),
    ]
```

**Files to modify**:
- Create: `docker/postgres/postgresql.conf` (development only)
- Modify: `docker-compose.yml` (development only)
- Modify: `config/settings/production.py` ✅ (already done)
- Create: `meetings/migrations/0009_tune_autovacuum.py` ✅ (already done)
- Create: `docs/crunchybridge-configuration.md` ✅ (already done)

**Production Action Required**:
Apply CrunchyBridge configuration via console/CLI (see docs/crunchybridge-configuration.md)

---

### 1.3 Redis Query Caching

**Problem**: Search queries follow a power-law distribution - a small number of popular queries account for most traffic. Every query hits the database unnecessarily.

**Solution**: Cache search results in Redis with 2-5 minute TTL.

**Implementation**:

Create `searches/cache.py`:
```python
"""
Redis-based caching for search results.

Caches full search result dictionaries to eliminate database load for repeated queries.
Uses a 5-minute TTL to balance freshness with cache hit rate.
"""

import hashlib
import json
from typing import Any

from django.core.cache import cache


def _make_search_cache_key(
    search_term: str,
    municipalities: list[int],
    states: list[str],
    date_from: str | None,
    date_to: str | None,
    document_type: str,
    meeting_name_query: str,
    limit: int,
    offset: int,
) -> str:
    """
    Generate a unique cache key for a search query.

    Uses MD5 hash of normalized parameters to create a consistent key.
    """
    # Normalize parameters for consistent hashing
    params = {
        "q": search_term.strip().lower() if search_term else "",
        "munis": sorted(municipalities) if municipalities else [],
        "states": sorted(states) if states else [],
        "date_from": date_from or "",
        "date_to": date_to or "",
        "doc_type": document_type,
        "meeting": meeting_name_query.strip().lower() if meeting_name_query else "",
        "limit": limit,
        "offset": offset,
    }

    # Create stable JSON representation
    params_json = json.dumps(params, sort_keys=True)
    params_hash = hashlib.md5(params_json.encode()).hexdigest()

    return f"search:v1:{params_hash}"


def get_cached_search_results(
    search_term: str = "",
    municipalities: list[int] | None = None,
    states: list[str] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    document_type: str = "all",
    meeting_name_query: str = "",
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int] | None:
    """
    Get cached search results if available.

    Returns:
        Tuple of (results, total_count) if cached, None if not cached.
    """
    cache_key = _make_search_cache_key(
        search_term=search_term,
        municipalities=municipalities or [],
        states=states or [],
        date_from=date_from,
        date_to=date_to,
        document_type=document_type,
        meeting_name_query=meeting_name_query,
        limit=limit,
        offset=offset,
    )

    return cache.get(cache_key)


def set_cached_search_results(
    results: list[dict[str, Any]],
    total_count: int,
    search_term: str = "",
    municipalities: list[int] | None = None,
    states: list[str] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    document_type: str = "all",
    meeting_name_query: str = "",
    limit: int = 100,
    offset: int = 0,
    timeout: int = 300,  # 5 minutes default
) -> None:
    """
    Cache search results with a TTL.

    Args:
        results: List of search result dictionaries
        total_count: Total number of matching results
        timeout: Cache TTL in seconds (default 5 minutes)
    """
    cache_key = _make_search_cache_key(
        search_term=search_term,
        municipalities=municipalities or [],
        states=states or [],
        date_from=date_from,
        date_to=date_to,
        document_type=document_type,
        meeting_name_query=meeting_name_query,
        limit=limit,
        offset=offset,
    )

    cache.set(cache_key, (results, total_count), timeout=timeout)


def invalidate_search_cache_for_municipality(municipality_id: int) -> None:
    """
    Invalidate all search cache entries for a specific municipality.

    Called when new documents are indexed for a municipality.

    Note: This is a naive implementation that clears the entire cache.
    For production with high write volume, consider more sophisticated
    invalidation using cache key patterns or versioning.
    """
    # Simple approach: clear entire search cache when any municipality updates
    # This is safe but may reduce cache hit rate
    # For better performance, use Redis key patterns (requires django-redis backend)
    cache.delete_pattern("search:v1:*")


def invalidate_all_search_caches() -> None:
    """
    Clear all search caches.

    Use sparingly - primarily for admin actions or bulk data updates.
    """
    cache.delete_pattern("search:v1:*")
```

Update `searches/services.py` to use cache:
```python
# Add to imports
from .cache import (
    get_cached_search_results,
    set_cached_search_results,
)


def execute_search_with_backend(search, limit=100, offset=0):
    """
    Execute a Search object using the configured backend, returning raw results.

    Uses Redis caching to eliminate database load for repeated queries.

    Args:
        search: Search model instance with filter configuration
        limit: Maximum number of results to return
        offset: Number of results to skip (for pagination)

    Returns:
        Tuple of (results, total_count)
        - results: List of dictionaries with page data
        - total_count: Total number of matching results
    """
    # Convert municipalities queryset to list of IDs for cache key
    muni_ids = list(search.municipalities.values_list("id", flat=True))

    # Try cache first
    cached = get_cached_search_results(
        search_term=search.search_term,
        municipalities=muni_ids,
        states=search.states,
        date_from=search.date_from.isoformat() if search.date_from else None,
        date_to=search.date_to.isoformat() if search.date_to else None,
        document_type=search.document_type,
        meeting_name_query=search.meeting_name_query,
        limit=limit,
        offset=offset,
    )

    if cached is not None:
        return cached

    # Cache miss - execute search
    from .search_backends import get_search_backend

    backend = get_search_backend()

    results, total = backend.search(
        query_text=search.search_term,
        municipalities=search.municipalities.all(),
        states=search.states,
        date_from=search.date_from,
        date_to=search.date_to,
        document_type=search.document_type,
        meeting_name_query=search.meeting_name_query,
        limit=limit,
        offset=offset,
    )

    # Cache the results
    set_cached_search_results(
        results=results,
        total_count=total,
        search_term=search.search_term,
        municipalities=muni_ids,
        states=search.states,
        date_from=search.date_from.isoformat() if search.date_from else None,
        date_to=search.date_to.isoformat() if search.date_to else None,
        document_type=search.document_type,
        meeting_name_query=search.meeting_name_query,
        limit=limit,
        offset=offset,
        timeout=300,  # 5 minutes
    )

    return results, total
```

Update settings to use django-redis:
```python
# config/settings/base.py

CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": env.str("REDIS_URL", "redis://redis:6379/1"),
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
            "PARSER_CLASS": "redis.connection.HiredisParser",
            "SOCKET_CONNECT_TIMEOUT": 5,
            "SOCKET_TIMEOUT": 5,
            "COMPRESSOR": "django_redis.compressors.zlib.ZlibCompressor",
            "CONNECTION_POOL_KWARGS": {
                "max_connections": 50,
                "retry_on_timeout": True,
            },
        },
        "KEY_PREFIX": "civicobs",
        "TIMEOUT": 300,  # 5 minutes default
    }
}
```

**Cache Invalidation Strategy**:

Add cache invalidation to backfill completion:
```python
# meetings/tasks.py


def backfill_meeting_data_for_municipality(municipality_id, document_type="agenda"):
    """Background task to backfill meeting data."""
    # ... existing implementation ...

    # At end of successful backfill:
    from searches.cache import invalidate_search_cache_for_municipality

    invalidate_search_cache_for_municipality(municipality_id)
```

**Files to modify**:
- Create: `searches/cache.py`
- Modify: `searches/services.py` (add caching to `execute_search_with_backend`)
- Modify: `config/settings/base.py` (add CACHES config)
- Modify: `meetings/tasks.py` (add cache invalidation)
- Add dependency: `uv add django-redis hiredis`

**Testing**:
```python
# tests/searches/test_cache.py
import pytest
from searches.cache import (
    get_cached_search_results,
    set_cached_search_results,
    invalidate_all_search_caches,
)


@pytest.mark.django_db
def test_search_cache_hit():
    """Test that cached results are returned on second call."""
    results = [{"id": "1", "text": "test"}]
    total = 1

    # Cache miss
    assert get_cached_search_results(search_term="test") is None

    # Set cache
    set_cached_search_results(
        results=results,
        total_count=total,
        search_term="test",
    )

    # Cache hit
    cached_results, cached_total = get_cached_search_results(search_term="test")
    assert cached_results == results
    assert cached_total == total


@pytest.mark.django_db
def test_cache_invalidation():
    """Test that cache invalidation clears results."""
    set_cached_search_results(
        results=[{"id": "1"}],
        total_count=1,
        search_term="test",
    )

    invalidate_all_search_caches()

    assert get_cached_search_results(search_term="test") is None
```

---

### Phase 1 Success Metrics

**Before Phase 1**:
- Query times: Variable, with spikes up to 1-3 seconds
- 95th percentile: ~500ms
- No cache hit rate

**After Phase 1 Target**:
- Query times: Consistent <100ms for cached queries
- 95th percentile: <200ms for uncached queries
- Cache hit rate: 70-85%
- Eliminated latency spikes from GIN pending list

**How to measure**:
```sql
-- Enable pg_stat_statements for monitoring
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

-- Query performance stats
SELECT
    query,
    calls,
    mean_exec_time,
    max_exec_time,
    stddev_exec_time
FROM pg_stat_statements
WHERE query LIKE '%search_vector%'
ORDER BY mean_exec_time DESC
LIMIT 10;
```

---

## Phase 2: PostgreSQL Polish (Next Sprint, 4-6 hours)

**Goal**: Handle edge cases (very broad queries, deep pagination) gracefully.
**Expected Outcome**: All query patterns complete in <500ms.

### 2.1 Capped Sampling for Broad Queries

**Problem**: Queries matching millions of rows still take seconds even without ranking, because PostgreSQL must fetch all matching IDs from the index.

**Solution**: Implement a two-phase search pattern for broad queries:
1. Match with `LIMIT 5000` (fast, index-only)
2. Optionally rank those 5,000 results
3. Return top N

**Implementation**:

Update `searches/services.py`:
```python
def _apply_full_text_search_with_sampling(queryset, query_text, sample_size=5000):
    """
    Apply full-text search with capped sampling for broad queries.

    For queries that match many documents, limit the working set to avoid
    scanning millions of rows. This trades perfect ranking for speed.

    Args:
        queryset: MeetingPage queryset to search
        query_text: Search query string
        sample_size: Maximum documents to consider (default 5000)

    Returns:
        Tuple of (filtered_queryset, search_query_object)
    """
    from django.db import connection

    # For now, use simple approach: just limit the queryset size
    # This gives us "good enough" results quickly
    search_query = SearchQuery(query_text, search_type="websearch", config="simple")

    queryset = queryset.filter(search_vector=search_query).order_by(
        "-document__meeting_date"
    )[:sample_size]

    return queryset, search_query


# Alternative: Raw SQL for precise control
def execute_search_with_sampling(search, limit=100, offset=0, sample_size=5000):
    """
    Execute search with capped sampling for broad queries.

    Uses raw SQL for precise control over the two-phase pattern.
    """
    from django.db import connection

    # Build filter conditions
    where_clauses = []
    params = []

    if search.search_term:
        where_clauses.append("search_vector @@ websearch_to_tsquery('simple', %s)")
        params.append(search.search_term)

    if search.municipalities.exists():
        muni_ids = list(search.municipalities.values_list("id", flat=True))
        where_clauses.append(f"document.municipality_id = ANY(%s)")
        params.append(muni_ids)

    # ... more filters ...

    where_sql = " AND ".join(where_clauses) if where_clauses else "TRUE"

    with connection.cursor() as cursor:
        # Two-phase query: match first (capped), then sort/paginate
        cursor.execute(
            f"""
            SELECT
                page.id,
                page.text,
                page.page_number,
                doc.meeting_date,
                doc.meeting_name,
                muni.name as municipality_name
            FROM (
                SELECT id, document_id, text, page_number
                FROM meetings_meetingpage page
                JOIN meetings_meetingdocument doc ON page.document_id = doc.id
                WHERE {where_sql}
                LIMIT %s
            ) page
            JOIN meetings_meetingdocument doc ON page.document_id = doc.id
            JOIN municipalities_muni muni ON doc.municipality_id = muni.id
            ORDER BY doc.meeting_date DESC
            LIMIT %s OFFSET %s
        """,
            params + [sample_size, limit, offset],
        )

        # Convert to dictionaries
        columns = [col[0] for col in cursor.description]
        results = [dict(zip(columns, row)) for row in cursor.fetchall()]

    return results, min(len(results), sample_size)
```

**When to use**:
- Queries with no search term ("all updates" mode): Already fast with date sorting
- Queries with very short terms (1-2 chars): May match millions of rows
- Solution: Add query analysis to detect broad queries and use sampling

**Files to modify**:
- Modify: `searches/services.py` (add sampling functions)
- Create: `searches/query_analyzer.py` (detect broad queries)

---

### 2.2 Keyset Pagination

**Problem**: `OFFSET 1000 LIMIT 20` forces PostgreSQL to compute and discard 1,000 rows. Deep pages get progressively slower.

**Solution**: Cursor-based pagination using `(meeting_date, id)` as a unique, sortable key.

**Implementation**:

Update search views to use keyset pagination:
```python
# meetings/views.py


def meeting_search_htmx(request):
    """HTMX endpoint for search results with keyset pagination."""
    form = MeetingSearchForm(request.GET)

    if not form.is_valid():
        return render(...)

    # Get cursor from request (base64-encoded date+id)
    cursor = request.GET.get("cursor")
    before_date = None
    before_id = None

    if cursor:
        try:
            import base64
            import json

            decoded = json.loads(base64.b64decode(cursor))
            before_date = decoded.get("date")
            before_id = decoded.get("id")
        except Exception:
            pass

    # Execute search with cursor
    results = execute_search_with_keyset(
        search=form.cleaned_data,
        before_date=before_date,
        before_id=before_id,
        limit=20,
    )

    # Generate next cursor
    if results:
        last_result = results[-1]
        next_cursor = base64.b64encode(
            json.dumps(
                {
                    "date": last_result["meeting_date"],
                    "id": last_result["id"],
                }
            ).encode()
        ).decode()
    else:
        next_cursor = None

    return render(
        request,
        "meetings/partials/search_results.html",
        {
            "results": results,
            "next_cursor": next_cursor,
        },
    )
```

**Files to modify**:
- Modify: `meetings/views.py` (add keyset pagination)
- Modify: `searches/services.py` (add cursor support)
- Modify: `templates/meetings/partials/search_results.html` (use cursor instead of page numbers)

---

### 2.3 Custom Stop Word Dictionary

**Problem**: Civic meeting documents have domain-specific common words ("motion", "second", "council", "meeting") that appear in almost every document, bloating the index and degrading relevance.

**Solution**: Create a custom text search configuration with civic-specific stop words.

**Implementation**:

Analyze corpus to find common terms:
```sql
-- Find most common terms in search_vector
SELECT word, ndoc, nentry
FROM ts_stat('SELECT search_vector FROM meetings_meetingpage')
ORDER BY ndoc DESC
LIMIT 100;
```

Create migration with custom config:
```python
# meetings/migrations/000X_custom_text_search_config.py
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("meetings", "000X_tune_autovacuum"),
    ]

    operations = [
        # Create custom text search configuration
        migrations.RunSQL(
            sql="""
                -- Copy simple config as base
                CREATE TEXT SEARCH CONFIGURATION civic (COPY = simple);

                -- Create custom stop word list
                CREATE TEXT SEARCH DICTIONARY civic_stopwords (
                    TEMPLATE = pg_catalog.simple,
                    STOPWORDS = civic
                );

                -- Apply stop words to all token types
                ALTER TEXT SEARCH CONFIGURATION civic
                    ALTER MAPPING FOR asciiword, asciihword, hword_asciipart,
                                      word, hword, hword_part
                    WITH civic_stopwords;
            """,
            reverse_sql="""
                DROP TEXT SEARCH CONFIGURATION IF EXISTS civic;
                DROP TEXT SEARCH DICTIONARY IF EXISTS civic_stopwords;
            """,
        ),
    ]
```

Create stop word file:
```bash
# $PGDATA/tsearch_data/civic.stop
motion
second
council
meeting
board
agenda
minutes
vote
approved
member
chair
```

Update triggers to use custom config:
```sql
-- Update existing triggers
CREATE OR REPLACE FUNCTION meetings_meetingpage_search_vector_trigger() RETURNS trigger AS $$
BEGIN
  NEW.search_vector := to_tsvector('civic', unaccent(coalesce(NEW.text, '')));
  RETURN NEW;
END
$$ LANGUAGE plpgsql;
```

**Impact**: 20-40% index size reduction, improved relevance for civic-specific queries.

**Files to modify**:
- Create: Migration to add custom text search config
- Modify: Existing trigger functions to use 'civic' config instead of 'simple'
- Create: Stop word file (may need manual PostgreSQL server setup)

---

### Phase 2 Success Metrics

**After Phase 2 Target**:
- All queries complete in <500ms (99th percentile)
- Deep pagination (page 50+) as fast as page 1 (<50ms)
- Reduced index size by 20-30%
- Improved relevance scores for civic-specific terms

---

## Phase 3: Evaluate External Search Engine (Future, 1-2 weeks)

**Trigger**: Only pursue if Phase 1+2 fail to meet performance goals.

**Decision Criteria**:
- 95th percentile query time > 500ms after Phase 1+2
- Need for advanced features (fuzzy search, faceting, typo tolerance)
- Corpus growing beyond 50M documents

### Option 3A: ParadeDB (pg_search extension)

**Pros**:
- 20-1,000× faster than PostgreSQL FTS at 10M+ rows
- BM25 ranking (superior to ts_rank)
- Fuzzy matching, highlighting built-in
- SQL interface - minimal code changes
- Data stays in PostgreSQL

**Cons**:
- Not available on all managed PostgreSQL providers (check AWS RDS, Google Cloud SQL, etc.)
- Relatively new project (less mature than Elasticsearch)
- May require self-hosted PostgreSQL

**Implementation Sketch**:
```sql
-- Create ParadeDB index
CALL paradedb.create_bm25(
    index_name => 'meeting_search',
    table_name => 'meetings_meetingpage',
    key_field => 'id',
    text_fields => '{
        "text": {},
        "meeting_name": {"boost": 2.0}
    }',
    numeric_fields => '{"page_number": {}}',
    datetime_fields => '{"meeting_date": {}}'
);

-- Query
SELECT * FROM meeting_search.search(
    'customs border patrol',
    limit_rows => 20
);
```

**Effort**: 2-3 days (if provider supports it), 1 week (if self-hosting)

---

### Option 3B: Meilisearch

**Note**: You previously attempted Meilisearch integration (commits f67ba79, ce58b20, a561a68). Consider what went wrong before:
- Infrastructure complexity?
- Sync pipeline issues?
- Cost concerns?

**Pros**:
- Sub-50ms queries at 10M+ documents
- Built-in typo tolerance (2 typos by default)
- Faceted search, filtering
- Simple REST API
- Single-binary deployment

**Cons**:
- Separate infrastructure to manage
- Data synchronization pipeline required
- Eventual consistency between PostgreSQL and Meilisearch
- Additional cost (hosting)

**Implementation Sketch**:

Sync pipeline (Django signals):
```python
# meetings/signals.py
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from .models import MeetingPage
from .search import index_page_in_meilisearch, delete_page_from_meilisearch


@receiver(post_save, sender=MeetingPage)
def sync_page_to_meilisearch(sender, instance, created, **kwargs):
    """Sync page to Meilisearch on create/update."""
    index_page_in_meilisearch(instance)


@receiver(post_delete, sender=MeetingPage)
def remove_page_from_meilisearch(sender, instance, **kwargs):
    """Remove page from Meilisearch on delete."""
    delete_page_from_meilisearch(instance.id)
```

**Effort**: 1-2 weeks (infrastructure + sync + testing)

---

### Option 3C: Typesense

**Similar to Meilisearch**:
- Lightweight, fast, typo-tolerant
- Single-binary deployment
- Good for 10M-100M documents

**Differentiation**:
- Slightly faster than Meilisearch on some benchmarks
- Better geo-search support
- More expensive cloud hosting

**Effort**: 1-2 weeks (same as Meilisearch)

---

## Monitoring & Observability

### Database Query Monitoring

Enable `pg_stat_statements`:
```sql
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

-- Reset stats
SELECT pg_stat_statements_reset();

-- After running searches for a while, check performance
SELECT
    substring(query, 1, 100) as query_preview,
    calls,
    mean_exec_time,
    max_exec_time,
    stddev_exec_time,
    rows
FROM pg_stat_statements
WHERE query LIKE '%search_vector%'
ORDER BY mean_exec_time DESC
LIMIT 20;
```

Enable `auto_explain` for slow queries:
```sql
-- In postgresql.conf or via ALTER SYSTEM
session_preload_libraries = 'auto_explain'
auto_explain.log_min_duration = '1s'
auto_explain.log_analyze = true
auto_explain.log_buffers = true
```

### Application-Level Monitoring

Use `django-silk` for production profiling:
```python
# config/settings/base.py (development only)
if DEBUG:
    INSTALLED_APPS += ["silk"]
    MIDDLEWARE.insert(0, "silk.middleware.SilkyMiddleware")
```

Add custom timing middleware:
```python
# searches/middleware.py
import time
import logging

logger = logging.getLogger(__name__)


class SearchTimingMiddleware:
    """Log search query performance."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith("/meetings/search"):
            start = time.time()
            response = self.get_response(request)
            duration = (time.time() - start) * 1000

            logger.info(
                "search_query",
                extra={
                    "duration_ms": duration,
                    "query": request.GET.get("query", ""),
                    "cache_hit": getattr(request, "_cache_hit", False),
                },
            )

            return response

        return self.get_response(request)
```

### Cache Hit Rate Monitoring

```python
# searches/cache.py additions
import logging

logger = logging.getLogger(__name__)


def get_cached_search_results(search_term, municipalities, limit=100):
    result = cache.get(cache_key)

    if result is not None:
        logger.info("search_cache_hit", extra={"cache_key": cache_key})
    else:
        logger.info("search_cache_miss", extra={"cache_key": cache_key})

    return result
```

**Target Metrics**:
- Cache hit rate: 70-85%
- Mean query time (uncached): <100ms
- 95th percentile (uncached): <200ms
- 99th percentile (uncached): <500ms

---

## Testing Strategy

### Performance Regression Tests

```python
# tests/searches/test_performance.py
import pytest
import time
from django.test import override_settings


@pytest.mark.django_db
@pytest.mark.slow
def test_search_performance_baseline(search_factory, meeting_page_factory):
    """Ensure search queries complete within performance budget."""
    # Create test data
    for _ in range(1000):
        meeting_page_factory()

    search = search_factory(search_term="budget")

    start = time.time()
    results = execute_search(search)
    duration = time.time() - start

    # Should complete in < 100ms even without cache
    assert duration < 0.1, f"Search took {duration:.3f}s, expected < 0.1s"
    assert results.count() > 0


@pytest.mark.django_db
def test_cache_hit_performance(search_factory):
    """Cached queries should be significantly faster."""
    search = search_factory(search_term="transit")

    # First query (cache miss)
    start = time.time()
    results1, total1 = execute_search_with_backend(search)
    uncached_duration = time.time() - start

    # Second query (cache hit)
    start = time.time()
    results2, total2 = execute_search_with_backend(search)
    cached_duration = time.time() - start

    # Cached should be at least 10× faster
    assert cached_duration < uncached_duration / 10
    assert results1 == results2
```

### Load Testing

Use Locust for realistic load testing:
```python
# locustfile.py
from locust import HttpUser, task, between


class SearchUser(HttpUser):
    wait_time = between(1, 3)

    @task(3)
    def search_common_term(self):
        """Simulate common search (should be cached)."""
        self.client.get("/meetings/search/?query=budget")

    @task(1)
    def search_rare_term(self):
        """Simulate unique search (cache miss)."""
        import random

        query = f"term_{random.randint(1, 10000)}"
        self.client.get(f"/meetings/search/?query={query}")
```

Run:
```bash
locust -f locustfile.py --host=http://localhost:8000 --users 50 --spawn-rate 5
```

---

## Rollback Plans

### Phase 1 Rollback

If Phase 1 changes cause issues:

**GIN index rollback**:
```sql
-- Revert to default fastupdate=True
DROP INDEX meetingpage_search_vector_idx;
CREATE INDEX meetingpage_search_vector_idx
ON meetings_meetingpage USING GIN (search_vector);
```

**PostgreSQL config rollback**:
- Remove `docker/postgres/postgresql.conf`
- Revert `docker-compose.yml` changes
- Restart database: `docker-compose restart db`

**Cache rollback**:
- Set `CACHES["default"]["BACKEND"]` to `django.core.cache.backends.dummy.DummyCache`
- This disables caching without code changes

### Phase 2 Rollback

**Sampling rollback**:
- Revert changes to `searches/services.py`
- No database changes needed

**Keyset pagination rollback**:
- Revert view changes
- Fall back to OFFSET pagination

---

## Future Considerations

### Alternative Approaches Not Pursued

1. **Materialized Views**: Pre-compute search results for common queries
   - **Rejected**: Refresh overhead, stale data, limited flexibility

2. **Table Partitioning by Date**: Separate tables per year
   - **Deferred**: Moderate benefit at current scale (12M rows)
   - **Revisit**: When corpus exceeds 50M rows

3. **RUM Index Extension**: Stores positions for true ranked index scans
   - **Deferred**: Not available on managed PostgreSQL
   - **Revisit**: If self-hosting and ranking is critical

### Scaling Beyond PostgreSQL

**When to migrate to external search**:
- Corpus grows beyond 100M documents
- Query times exceed 500ms at 99th percentile after all optimizations
- Need advanced features (ML ranking, faceted search, geo-search)
- Team has bandwidth for distributed system operations

**Recommended path**: PostgreSQL → ParadeDB → Elasticsearch
- Try ParadeDB first (least operational burden)
- Move to Elasticsearch only if ParadeDB insufficient

---

## References

### Research Documents
- Original research artifact: [Claude AI artifact d207a706]
- GitLab GIN pending list case study
- Xata.io PostgreSQL FTS benchmarks
- Adam Johnson JIT performance analysis

### Code References
- `meetings/models.py:74-114` - MeetingPage model with search_vector
- `searches/services.py:319-346` - Current search implementation
- `meetings/migrations/0003_optimize_fulltext_search.py` - Initial FTS setup
- `meetings/migrations/0004_meetingdocument_meeting_name_search_vector.py` - Meeting name search

### External Resources
- [PostgreSQL Full-Text Search Documentation](https://www.postgresql.org/docs/current/textsearch.html)
- [GIN Index Documentation](https://www.postgresql.org/docs/current/gin-intro.html)
- [ParadeDB Documentation](https://docs.paradedb.com/)
- [Meilisearch Documentation](https://docs.meilisearch.com/)

---

## Changelog

- **2026-02-06**: Initial plan created based on research findings
- **2026-02-06**: Phase 1 implementation started
