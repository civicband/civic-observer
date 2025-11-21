# MeetingPage Partitioning Strategy for 100M+ Rows

**Date**: 2025-11-20
**Context**: Detailed analysis of table partitioning approaches for scaling MeetingPage to 100+ million rows with Django ORM compatibility
**Related**: See [SCALING_100M_PAGES.md](SCALING_100M_PAGES.md) for overall scaling strategy

---

## Executive Summary

PostgreSQL native table partitioning is critical for scaling to 100M+ rows, but Django has **no native support** for partitioned tables. However, partitioning works **transparently with Django ORM queries** once implemented correctly.

**Key Findings**:
- ✅ Queries work unchanged - Django doesn't need to know about partitions
- ⚠️ Cannot partition by `document__meeting_date` directly (not a column on MeetingPage)
- ⚠️ Requires composite primary key: `PRIMARY KEY (id, partition_key)`
- ⚠️ Must use raw SQL migrations or third-party package
- ⚠️ PostgreSQL doesn't support foreign keys TO partitioned tables

**Recommended Approach**: Denormalize `meeting_date` to MeetingPage and partition by RANGE on that field using raw SQL migrations.

---

## The Core Problem

The SCALING_100M_PAGES.md document proposes:

```sql
PARTITION BY RANGE (document__meeting_date)  -- ❌ This won't work
```

**Why it fails**: PostgreSQL requires the partition key to be **a direct column in the partitioned table**, not a column accessed via JOIN.

Current schema:
```python
class MeetingPage(TimeStampedModel):
    id = models.CharField(max_length=255, primary_key=True)
    document = models.ForeignKey(MeetingDocument, ...)  # meeting_date is HERE
    page_number = models.IntegerField()
    text = models.TextField()
    search_vector = SearchVectorField()
```

The `meeting_date` field lives on `MeetingDocument`, not `MeetingPage`.

---

## Django ORM Compatibility Analysis

### What Works Transparently ✅

Once partitioning is set up, **all standard Django ORM queries work unchanged**:

```python
# All these work exactly as they do now
MeetingPage.objects.filter(search_vector=search_query)
MeetingPage.objects.select_related("document", "document__municipality")
MeetingPage.objects.create(id="...", document=doc, ...)
page.save()
page.delete()
MeetingDocument.objects.prefetch_related("meetingpage_set")
```

PostgreSQL handles partition routing and pruning automatically.

### What Requires Special Handling ⚠️

#### 1. Composite Primary Key Requirement

PostgreSQL requires the partition key to be part of the primary key:

```sql
-- Current (non-partitioned)
PRIMARY KEY (id)

-- Required for partitioning
PRIMARY KEY (id, meeting_date)  -- Must include partition key
```

**Django Compatibility**:
- Django 5.1 and earlier: Cannot model composite PKs (use `managed=False`)
- Django 5.2+: Has experimental `CompositePrimaryKey` support
- **Solution**: Use `managed=False` and manage table structure manually

#### 2. Migration Management

Django's `makemigrations` and `migrate` cannot create partitioned tables.

**Options**:
- **Raw SQL migrations** using `migrations.RunSQL()` (recommended for this project)
- **django-postgres-extra** package (adds external dependency)

#### 3. Foreign Key Limitations

**Critical**: PostgreSQL does **NOT** support foreign keys **TO** partitioned tables.

**Impact**:
- If MeetingPage is partitioned, nothing can have `ForeignKey(MeetingPage)` at DB level
- Currently, nothing references MeetingPage, so this is fine
- Future features (comments, bookmarks) must use `db_constraint=False`

#### 4. Partition Key Selection

The partition key must be:
- A direct column on MeetingPage (not from a JOIN)
- Included in most WHERE clauses for partition pruning to work
- Stable (cannot be updated easily across partitions)

---

## Partitioning Options

### Option A: Denormalize `meeting_date` to MeetingPage ⭐ RECOMMENDED

**Approach**: Add `meeting_date` field directly to MeetingPage (copy from document).

#### Schema Changes

```python
class MeetingPage(TimeStampedModel):
    id = models.CharField(max_length=255, primary_key=True)
    document = models.ForeignKey(
        MeetingDocument,
        on_delete=models.CASCADE,
        db_constraint=False,  # No FK constraint at DB level
    )
    meeting_date = models.DateField(db_index=True)  # ← DENORMALIZED from document
    page_number = models.IntegerField()
    text = models.TextField(blank=True)
    search_vector = SearchVectorField(null=True)

    class Meta:
        managed = False  # We manage the partitioned table manually
        db_table = "meetings_meetingpage"
```

#### Migration Implementation

```python
# migrations/0005_partition_meetingpage.py
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("meetings", "0004_previous_migration")]

    operations = [
        # Step 1: Add meeting_date column to existing table (if has data)
        migrations.RunSQL(
            sql="""
                -- Add denormalized field
                ALTER TABLE meetings_meetingpage
                ADD COLUMN meeting_date DATE;

                -- Populate from document
                UPDATE meetings_meetingpage mp
                SET meeting_date = md.meeting_date
                FROM meetings_meetingdocument md
                WHERE mp.document_id = md.id;

                -- Make it NOT NULL
                ALTER TABLE meetings_meetingpage
                ALTER COLUMN meeting_date SET NOT NULL;
            """,
            reverse_sql="ALTER TABLE meetings_meetingpage DROP COLUMN meeting_date;",
        ),
        # Step 2: Create partitioned table
        migrations.RunSQL(
            sql="""
                -- Create new partitioned table
                CREATE TABLE meetings_meetingpage_new (
                    id VARCHAR(255),
                    document_id BIGINT NOT NULL,
                    meeting_date DATE NOT NULL,
                    page_number INTEGER NOT NULL,
                    text TEXT,
                    search_vector tsvector,
                    created TIMESTAMP NOT NULL,
                    modified TIMESTAMP NOT NULL,
                    PRIMARY KEY (id, meeting_date)
                ) PARTITION BY RANGE (meeting_date);
            """,
        ),
        # Step 3: Create monthly partitions
        migrations.RunSQL(
            sql="""
                -- 2023 partitions
                CREATE TABLE meetings_meetingpage_2023_01 PARTITION OF meetings_meetingpage_new
                    FOR VALUES FROM ('2023-01-01') TO ('2023-02-01');
                CREATE TABLE meetings_meetingpage_2023_02 PARTITION OF meetings_meetingpage_new
                    FOR VALUES FROM ('2023-02-01') TO ('2023-03-01');
                -- ... create all 2023 partitions

                -- 2024 partitions
                CREATE TABLE meetings_meetingpage_2024_01 PARTITION OF meetings_meetingpage_new
                    FOR VALUES FROM ('2024-01-01') TO ('2024-02-01');
                -- ... create all 2024 partitions

                -- 2025 partitions (future)
                CREATE TABLE meetings_meetingpage_2025_01 PARTITION OF meetings_meetingpage_new
                    FOR VALUES FROM ('2025-01-01') TO ('2025-02-01');
                -- ... create future partitions

                -- Default partition for anything outside ranges
                CREATE TABLE meetings_meetingpage_default PARTITION OF meetings_meetingpage_new
                    DEFAULT;
            """,
        ),
        # Step 4: Copy data to partitioned table
        migrations.RunSQL(
            sql="""
                INSERT INTO meetings_meetingpage_new
                SELECT * FROM meetings_meetingpage;
            """,
        ),
        # Step 5: Swap tables (requires brief downtime)
        migrations.RunSQL(
            sql="""
                DROP TABLE meetings_meetingpage CASCADE;
                ALTER TABLE meetings_meetingpage_new RENAME TO meetings_meetingpage;
            """,
        ),
        # Step 6: Recreate indexes on partitioned table
        migrations.RunSQL(
            sql="""
                -- GIN index for full-text search (created on each partition)
                CREATE INDEX meetings_meetingpage_search_vector_gin
                ON meetings_meetingpage USING GIN (search_vector)
                WITH (fillfactor = 90);

                -- Index for document relationship
                CREATE INDEX meetings_meetingpage_document_id
                ON meetings_meetingpage (document_id);

                -- Covering index for common queries
                CREATE INDEX meetings_meetingpage_document_created_idx
                ON meetings_meetingpage (document_id, created DESC)
                INCLUDE (id, page_number);

                -- Statistics for query planner
                ALTER TABLE meetings_meetingpage
                ALTER COLUMN search_vector SET STATISTICS 1000;

                ALTER TABLE meetings_meetingpage
                ALTER COLUMN document_id SET STATISTICS 1000;
            """,
        ),
    ]
```

#### Partition Management Command

```python
# meetings/management/commands/create_meetingpage_partitions.py
from django.core.management.base import BaseCommand
from django.db import connection
from datetime import datetime, timedelta


class Command(BaseCommand):
    help = "Create future monthly partitions for MeetingPage table"

    def add_arguments(self, parser):
        parser.add_argument(
            "--months",
            type=int,
            default=12,
            help="Number of future months to create partitions for (default: 12)",
        )

    def handle(self, *args, **options):
        months = options["months"]

        # Start from first day of current month
        start_date = datetime.now().replace(day=1)

        with connection.cursor() as cursor:
            for month_offset in range(months):
                # Calculate partition date range
                partition_date = start_date + timedelta(days=30 * month_offset)
                # Ensure we're on the 1st of the month
                partition_date = partition_date.replace(day=1)

                # Calculate next month
                if partition_date.month == 12:
                    next_month = partition_date.replace(
                        year=partition_date.year + 1, month=1
                    )
                else:
                    next_month = partition_date.replace(month=partition_date.month + 1)

                partition_name = (
                    f"meetings_meetingpage_{partition_date.strftime('%Y_%m')}"
                )

                # Check if partition already exists
                cursor.execute(
                    """
                    SELECT 1 FROM pg_tables
                    WHERE tablename = %s
                """,
                    [partition_name],
                )

                if cursor.fetchone():
                    self.stdout.write(
                        self.style.WARNING(
                            f"Partition already exists: {partition_name}"
                        )
                    )
                    continue

                # Create partition
                cursor.execute(
                    f"""
                    CREATE TABLE {partition_name}
                    PARTITION OF meetings_meetingpage
                    FOR VALUES FROM ('{partition_date.date()}') TO ('{next_month.date()}')
                """
                )

                self.stdout.write(
                    self.style.SUCCESS(f"Created partition: {partition_name}")
                )

        self.stdout.write(
            self.style.SUCCESS(f"\nCreated {months} monthly partitions successfully")
        )
```

**Cron job** (run monthly):
```bash
0 0 1 * * cd /app && python manage.py create_meetingpage_partitions
```

#### Query Patterns and Performance

**With date filter (partition pruning works):**
```python
# Django ORM
MeetingPage.objects.filter(
    search_vector=search_query,
    meeting_date__gte="2024-01-01",
    meeting_date__lt="2024-04-01",
)

# PostgreSQL scans only: meetingpage_2024_01, meetingpage_2024_02, meetingpage_2024_03
# Expected: 10-20x faster (3 partitions vs. 36 partitions)
```

**Without date filter (scans all partitions):**
```python
# Django ORM
MeetingPage.objects.filter(search_vector=search_query)

# PostgreSQL scans all 36 partitions in parallel
# Expected: 2-3x faster (parallel scanning, smaller indexes, better caching)
```

**With document filter (common pattern):**
```python
# Django ORM
MeetingPage.objects.filter(document__municipality_id=123, search_vector=search_query)

# No automatic partition pruning (meeting_date not in WHERE clause)
# But: GIN index on search_vector still provides main optimization
# Expected: 2-3x faster (same as without date filter)
```

**Optimal pattern (add date filter when possible):**
```python
# Django ORM - encourage this pattern
MeetingPage.objects.filter(
    document__municipality_id=123,
    meeting_date__gte="2024-01-01",  # ← Enables partition pruning
    search_vector=search_query,
)

# Expected: 5-10x faster (partition pruning + GIN index)
```

#### Maintaining Denormalization

Add signal to keep `meeting_date` in sync:

```python
# meetings/signals.py
from django.db.models.signals import pre_save
from django.dispatch import receiver
from .models import MeetingPage


@receiver(pre_save, sender=MeetingPage)
def sync_meeting_date(sender, instance, **kwargs):
    """Ensure meeting_date stays in sync with document.meeting_date."""
    if instance.document_id:
        instance.meeting_date = instance.document.meeting_date
```

Or enforce at service layer:

```python
# meetings/services.py
def create_meeting_page(document, page_number, text):
    """Create a MeetingPage with proper denormalized fields."""
    return MeetingPage.objects.create(
        id=f"{document.id}_{page_number}",
        document=document,
        meeting_date=document.meeting_date,  # Denormalize
        page_number=page_number,
        text=text,
    )
```

#### Pros ✅

- **Direct partition pruning**: Queries with date filters benefit immediately
- **No JOIN required**: Eliminates one join for date-based queries
- **Archival capability**: Old partitions can be detached/compressed
- **Maintenance speed**: VACUUM/ANALYZE per partition is fast
- **Aligns with data semantics**: Pages belong to meetings on specific dates

#### Cons ⚠️

- **Data duplication**: 4 bytes per row × 100M rows = 400MB (negligible)
- **Sync requirement**: Must keep `meeting_date` in sync with `document.meeting_date`
- **Schema change**: Requires migration to add column
- **Composite PK**: Django model can't represent this accurately

---

### Option B: Partition by `created` Timestamp

**Approach**: Use the existing `created` field from `TimeStampedModel`.

#### Schema Changes

```python
class MeetingPage(TimeStampedModel):
    # No changes needed - 'created' already exists
    id = models.CharField(max_length=255, primary_key=True)
    document = models.ForeignKey(MeetingDocument, on_delete=models.CASCADE)
    page_number = models.IntegerField()
    text = models.TextField(blank=True)
    search_vector = SearchVectorField(null=True)

    class Meta:
        managed = False
        db_table = "meetings_meetingpage"
```

#### Partition Strategy

```sql
CREATE TABLE meetings_meetingpage (...)
PARTITION BY RANGE (created);  -- When page was ingested, not when meeting occurred

-- Monthly partitions based on ingestion date
CREATE TABLE meetings_meetingpage_2024_01 PARTITION OF meetings_meetingpage
    FOR VALUES FROM ('2024-01-01') TO ('2024-02-01');
```

#### Query Patterns

**With created filter (partition pruning works):**
```python
# Queries for recently ingested pages
MeetingPage.objects.filter(created__gte="2024-01-01", search_vector=search_query)
# Scans only recent partitions
```

**Without created filter (common case):**
```python
# Search by meeting date (NOT partition key)
MeetingPage.objects.filter(
    document__meeting_date__gte="2024-01-01", search_vector=search_query
)
# Must scan ALL partitions (no partition pruning)
```

#### Pros ✅

- **No schema changes**: `created` field already exists
- **No denormalization**: No data sync required
- **Simple migration**: Just repartition existing table
- **Works immediately**: Can implement today

#### Cons ⚠️

- **Poor partition pruning**: Most queries filter by `meeting_date`, not `created`
- **Semantic mismatch**: Partitions based on ingestion time, not meeting time
- **Backfill problem**: Historical data backfill puts all data in one partition
- **Archive complexity**: Can't archive by "old meetings" - only by "old ingestion"

#### When to Choose This Option

- **Backfill is not a concern**: If you're starting fresh without historical data
- **Recent data access pattern**: If most queries are for recently-ingested data
- **Quick implementation**: Need partitioning now without schema changes

---

### Option C: No Partitioning (Index-Only Optimization)

**Approach**: Skip partitioning entirely; focus on advanced indexing.

#### Index Strategy

```sql
-- 1. Optimized GIN index for full-text search
CREATE INDEX meetings_meetingpage_search_vector_gin
ON meetings_meetingpage USING GIN (search_vector)
WITH (fillfactor = 90);

-- 2. Covering index for search results (avoids table lookups)
CREATE INDEX meetings_meetingpage_search_covering_idx
ON meetings_meetingpage (document_id, created DESC)
INCLUDE (id, page_number);

-- 3. Partial index for recent data (most common searches)
CREATE INDEX meetings_meetingpage_recent_search_idx
ON meetings_meetingpage USING GIN (search_vector)
WHERE created > NOW() - INTERVAL '2 years';

-- 4. Composite index for common filter patterns
CREATE INDEX meetings_meetingpage_doc_date_idx
ON meetings_meetingpage (document_id)
INCLUDE (created, page_number);

-- 5. Increase statistics for better query planning
ALTER TABLE meetings_meetingpage
ALTER COLUMN search_vector SET STATISTICS 1000;
```

#### Configuration Tuning

```conf
# postgresql.conf optimizations for large tables
maintenance_work_mem = 2GB           # For index creation
autovacuum_vacuum_scale_factor = 0.01  # More aggressive VACUUM
autovacuum_analyze_scale_factor = 0.005
```

#### Pros ✅

- **Simplest architecture**: No migration complexity
- **Standard Django**: Works with normal migrations
- **Foreign keys allowed**: No partitioning restrictions
- **Easier testing**: Standard table structure

#### Cons ⚠️

- **VACUUM takes hours**: Maintenance on 100M row table is slow
- **No archival strategy**: All data stays in one table forever
- **Index maintenance**: Rebuilding GIN index on 100M rows is expensive
- **May not scale**: Beyond 100M rows, might hit performance limits

#### When to Choose This Option

- **Uncertain about growth**: If 100M is the absolute maximum
- **Simplicity priority**: If operational complexity is a bigger concern than performance
- **Temporary solution**: While evaluating partitioning approaches

---

## Comparison Matrix

| Factor | Option A: Denormalize | Option B: created | Option C: No Partition |
|--------|----------------------|-------------------|------------------------|
| **Query Performance** | ⭐⭐⭐⭐⭐ Best (with date filters) | ⭐⭐⭐ Medium (limited pruning) | ⭐⭐⭐ Medium (good indexes) |
| **Partition Pruning** | ✅ Works with date filters | ⚠️ Only with created filters | ❌ N/A |
| **Maintenance Speed** | ⭐⭐⭐⭐⭐ Fast (per partition) | ⭐⭐⭐⭐⭐ Fast (per partition) | ⭐⭐ Slow (full table) |
| **Archival Capability** | ✅ By meeting date | ⚠️ By ingestion date | ❌ None |
| **Schema Changes** | ⚠️ Add meeting_date field | ✅ None needed | ✅ None needed |
| **Data Sync** | ⚠️ Must sync meeting_date | ✅ No sync needed | ✅ No sync needed |
| **Migration Complexity** | ⭐⭐⭐ Medium (raw SQL) | ⭐⭐⭐ Medium (raw SQL) | ⭐⭐⭐⭐⭐ Simple (standard) |
| **Django Compatibility** | ⚠️ managed=False | ⚠️ managed=False | ✅ Full compatibility |
| **Backfill Handling** | ✅ Distributes properly | ❌ All in one partition | ✅ Works normally |
| **Operational Overhead** | ⭐⭐⭐ Medium | ⭐⭐⭐ Medium | ⭐⭐⭐⭐⭐ Low |

---

## Recommendation: Option A (Denormalize meeting_date)

### Why Option A is Best

1. **Semantic correctness**: Partitions align with how data is queried (by meeting date)
2. **Optimal performance**: Partition pruning works for date-filtered queries
3. **Archival strategy**: Can detach partitions older than 2 years
4. **Maintenance efficiency**: VACUUM/ANALYZE on 3M-row partitions vs. 100M-row table
5. **Acceptable tradeoffs**: 400MB storage overhead is negligible; sync is simple

### Implementation Roadmap

1. **Week 1: Add denormalized field**
   - Add `meeting_date` column to MeetingPage
   - Populate from document relationship
   - Add signal/service layer to maintain sync
   - Deploy and monitor

2. **Week 2: Create partitioned table**
   - Create partitioned version with raw SQL migration
   - Create 36 monthly partitions (2 years historical + 1 year future)
   - Copy data to partitioned table
   - Test queries on partitioned table

3. **Week 3: Swap tables**
   - Schedule maintenance window
   - Swap old table with partitioned version
   - Recreate indexes
   - Verify query performance
   - Monitor for issues

4. **Week 4: Partition management**
   - Create `create_meetingpage_partitions` management command
   - Set up monthly cron job
   - Document partition maintenance procedures
   - Create archival process for old partitions

### Success Metrics

- Query time with date filter: **10-20x faster**
- Query time without date filter: **2-3x faster**
- VACUUM time: **50-100x faster** (minutes vs. hours)
- Disk space: **+5-10%** (denormalized field + partition overhead)

---

## PostgreSQL Configuration for Partitioning

Add to `docker/postgresql.conf`:

```conf
# Partition-specific settings
enable_partition_pruning = on             # Enable partition elimination
constraint_exclusion = partition          # Exclude partitions based on constraints
enable_partitionwise_join = on            # Join partitions in parallel
enable_partitionwise_aggregate = on       # Aggregate within partitions

# Parallel query settings (for multi-partition scans)
max_parallel_workers_per_gather = 4       # Use 4 workers per query
max_parallel_workers = 8                  # Total parallel workers
parallel_setup_cost = 100
parallel_tuple_cost = 0.01
min_parallel_table_scan_size = 8MB        # Parallelize tables >8MB
min_parallel_index_scan_size = 512kB      # Parallelize indexes >512KB

# For queries without date filters (scan all partitions)
force_parallel_mode = off                 # Let planner decide when to parallelize
```

---

## Monitoring Partition Performance

### Query to Check Partition Pruning

```sql
-- Check which partitions are scanned for a query
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM meetings_meetingpage
WHERE meeting_date >= '2024-01-01'
  AND meeting_date < '2024-04-01'
  AND search_vector @@ to_tsquery('budget');

-- Look for:
-- "Subplans Removed: N" - partitions excluded by pruning
-- "Parallel Seq Scan on meetingpage_2024_01" - parallel scanning
```

### Monitor Partition Sizes

```sql
-- Check size of each partition
SELECT
    schemaname,
    tablename,
    pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) AS size,
    pg_total_relation_size(schemaname||'.'||tablename) AS bytes
FROM pg_tables
WHERE tablename LIKE 'meetings_meetingpage_%'
ORDER BY bytes DESC;
```

### Track Query Performance by Partition

```sql
-- Requires pg_stat_statements extension
SELECT
    query,
    calls,
    mean_exec_time,
    max_exec_time
FROM pg_stat_statements
WHERE query LIKE '%meetings_meetingpage%'
ORDER BY mean_exec_time DESC
LIMIT 20;
```

---

## Common Pitfalls and Solutions

### Pitfall 1: Forgetting to Create Future Partitions

**Problem**: Queries fail when inserting data outside existing partition ranges.

**Solution**:
- Create DEFAULT partition to catch overflow
- Run `create_meetingpage_partitions` monthly via cron
- Monitor logs for warnings about DEFAULT partition usage

### Pitfall 2: Queries Without Partition Key

**Problem**: Searches without date filter scan all partitions.

**Solution**:
- Document that date filters improve performance
- Add UI elements to encourage date filtering
- Monitor slow queries; add partial indexes for common patterns

### Pitfall 3: Composite Primary Key Confusion

**Problem**: Django models show `id` as primary key, but database has composite key.

**Solution**:
- Use `managed=False` to signal manual table management
- Document the discrepancy in model docstrings
- Accept that Django admin may not reflect true DB structure

### Pitfall 4: Foreign Key to Partitioned Table

**Problem**: Future features try to add ForeignKey(MeetingPage).

**Solution**:
- Document restriction prominently
- Use `db_constraint=False` for relationships
- Handle referential integrity at application level

### Pitfall 5: Forgetting to Update meeting_date

**Problem**: `meeting_date` gets out of sync with `document.meeting_date`.

**Solution**:
- Add pre_save signal to sync automatically
- Add database trigger as safety net
- Add data validation checks in tests

---

## Testing Strategy

### Unit Tests for Partitioned Queries

```python
# meetings/tests/test_partitioned_queries.py
from django.test import TestCase
from meetings.models import MeetingPage, MeetingDocument


class PartitionedQueryTests(TestCase):
    def test_partition_pruning_with_date_filter(self):
        """Verify partition pruning works with date filter."""
        from django.db import connection

        with self.assertNumQueries(1):
            # Query with date filter should use partition pruning
            pages = list(
                MeetingPage.objects.filter(
                    meeting_date__gte="2024-01-01", meeting_date__lt="2024-02-01"
                )
            )

        # Check EXPLAIN output to verify pruning
        with connection.cursor() as cursor:
            cursor.execute(
                """
                EXPLAIN (FORMAT JSON)
                SELECT * FROM meetings_meetingpage
                WHERE meeting_date >= '2024-01-01'
                  AND meeting_date < '2024-02-01'
            """
            )
            plan = cursor.fetchone()[0]

        # Verify only specific partitions were scanned
        self.assertIn("meetingpage_2024_01", str(plan))
        self.assertNotIn("meetingpage_2024_02", str(plan))

    def test_meeting_date_sync(self):
        """Verify meeting_date stays in sync with document."""
        doc = MeetingDocument.objects.create(
            municipality=self.muni,
            meeting_date="2024-01-15",
            meeting_name="City Council",
            document_type="agenda",
        )

        page = MeetingPage.objects.create(
            id="test_page_1",
            document=doc,
            meeting_date=doc.meeting_date,
            page_number=1,
            text="Test content",
        )

        # Verify sync
        self.assertEqual(page.meeting_date, doc.meeting_date)

        # Update document date
        doc.meeting_date = "2024-01-20"
        doc.save()

        # Re-save page (signal should sync)
        page.save()
        page.refresh_from_db()

        self.assertEqual(page.meeting_date, doc.meeting_date)
```

### Load Testing Partitioned Queries

```python
# Load test script
import time
from django.db import connection


def benchmark_partitioned_query(date_filter=True):
    """Benchmark query performance with and without date filter."""

    if date_filter:
        query = """
            SELECT * FROM meetings_meetingpage
            WHERE meeting_date >= '2024-01-01'
              AND meeting_date < '2024-04-01'
              AND search_vector @@ to_tsquery('budget')
            LIMIT 20
        """
    else:
        query = """
            SELECT * FROM meetings_meetingpage
            WHERE search_vector @@ to_tsquery('budget')
            LIMIT 20
        """

    with connection.cursor() as cursor:
        start = time.time()
        cursor.execute(query)
        results = cursor.fetchall()
        duration = time.time() - start

    print(f"Query {'with' if date_filter else 'without'} date filter:")
    print(f"  Duration: {duration:.3f}s")
    print(f"  Results: {len(results)} rows")

    return duration


# Run benchmark
with_date = benchmark_partitioned_query(date_filter=True)
without_date = benchmark_partitioned_query(date_filter=False)

print(f"\nSpeedup with date filter: {without_date / with_date:.1f}x")
```

---

## Rollback Plan

### If Partitioning Fails

1. **Keep old table during migration**:
   ```sql
   -- Don't drop old table immediately
   ALTER TABLE meetings_meetingpage RENAME TO meetings_meetingpage_backup;
   ```

2. **Test thoroughly before final swap**:
   - Run all tests against partitioned table
   - Benchmark query performance
   - Verify data integrity

3. **Quick rollback** (if issues found):
   ```sql
   DROP TABLE meetings_meetingpage;
   ALTER TABLE meetings_meetingpage_backup RENAME TO meetings_meetingpage;
   ```

4. **Monitor after deployment**:
   - Watch query performance metrics
   - Check error logs for partition-related issues
   - Monitor disk space usage

---

## Conclusion

**Recommended Approach**: Option A (Denormalize `meeting_date` to MeetingPage and partition by RANGE)

This provides the best balance of:
- ✅ Query performance (10-20x faster with date filters)
- ✅ Semantic correctness (partitions match query patterns)
- ✅ Archival capability (detach old partitions)
- ✅ Maintenance efficiency (fast VACUUM/ANALYZE)
- ✅ Django compatibility (queries work transparently)

The 400MB storage overhead and denormalization sync requirement are acceptable tradeoffs for the significant performance and operational benefits.

**Next Steps**: See implementation checklist in [SCALING_100M_PAGES.md](SCALING_100M_PAGES.md) Phase 2.
