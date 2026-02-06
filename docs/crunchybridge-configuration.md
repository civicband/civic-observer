# CrunchyBridge PostgreSQL Configuration for Search Optimization

This document outlines the PostgreSQL configuration changes needed on CrunchyBridge for optimal full-text search performance.

## Overview

Since CrunchyBridge is a managed service, we cannot directly edit `postgresql.conf`. Instead, we configure parameters via:
1. **CrunchyBridge web console/CLI** - for cluster-wide settings
2. **Django connection OPTIONS** - for per-connection settings (already configured in production.py)

## Required CrunchyBridge Settings

### Access the Configuration

**Via Web Console**:
1. Go to https://www.crunchybridge.com/
2. Select your cluster
3. Navigate to "Configuration" or "Settings"

**Via CLI**:
```bash
cb config set <cluster-id> <parameter> <value>
```

### Critical Settings to Apply

These are the most important settings for FTS performance:

```ini
# JIT Compilation - DISABLE (adds 50-200ms overhead per query)
jit = off

# Memory Settings (adjust based on your cluster size)
# For 8GB RAM cluster:
shared_buffers = 2GB              # 25% of RAM
work_mem = 64MB                   # Per-operation memory
maintenance_work_mem = 512MB      # For index builds
effective_cache_size = 6GB        # 75% of RAM

# For 32GB RAM cluster:
# shared_buffers = 8GB
# work_mem = 64MB
# maintenance_work_mem = 2GB
# effective_cache_size = 24GB

# SSD Optimization
random_page_cost = 1.1            # CrunchyBridge uses SSDs
effective_io_concurrency = 200    # Enable I/O prefetching

# Planner Statistics
default_statistics_target = 100   # Better query plans
```

### Apply Settings via CLI

If you have the CrunchyBridge CLI installed:

```bash
# Get your cluster ID
cb cluster list

# Apply settings (replace <cluster-id> with your cluster)
cb config set <cluster-id> jit off
cb config set <cluster-id> random_page_cost 1.1
cb config set <cluster-id> effective_io_concurrency 200
cb config set <cluster-id> work_mem '64MB'
cb config set <cluster-id> maintenance_work_mem '512MB'

# Verify settings
cb config list <cluster-id>
```

### Settings Already Configured in Django

These are set in `config/settings/production.py` via connection OPTIONS:
- `jit=off` - Redundant with cluster setting above, but ensures it's off
- `work_mem=64MB` - Per-connection override

## Per-Table Autovacuum Settings

These are applied via migrations (0009_tune_autovacuum.py) and don't require CrunchyBridge console access:

```sql
ALTER TABLE meetings_meetingpage SET (
    autovacuum_vacuum_scale_factor = 0.02,
    autovacuum_analyze_scale_factor = 0.01,
    autovacuum_vacuum_cost_limit = 2000,
    autovacuum_vacuum_cost_delay = 2
);
```

Run migrations to apply: `python manage.py migrate`

## Enable pg_stat_statements Extension

For query performance monitoring:

```sql
-- Connect to your database
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
```

Check performance after changes:
```sql
SELECT
    substring(query, 1, 100) as query_preview,
    calls,
    mean_exec_time,
    max_exec_time,
    stddev_exec_time
FROM pg_stat_statements
WHERE query LIKE '%search_vector%'
ORDER BY mean_exec_time DESC
LIMIT 20;
```

## Memory Sizing Guidelines

CrunchyBridge clusters by size:

| Cluster RAM | shared_buffers | work_mem | maintenance_work_mem | effective_cache_size |
|-------------|----------------|----------|---------------------|---------------------|
| 4 GB        | 1 GB           | 32 MB    | 256 MB              | 3 GB                |
| 8 GB        | 2 GB           | 64 MB    | 512 MB              | 6 GB                |
| 16 GB       | 4 GB           | 64 MB    | 1 GB                | 12 GB               |
| 32 GB       | 8 GB           | 64 MB    | 2 GB                | 24 GB               |
| 64 GB       | 16 GB          | 64 MB    | 2 GB                | 48 GB               |

## Verification

After applying settings, verify they're active:

```sql
-- Check JIT is off
SHOW jit;

-- Check memory settings
SHOW shared_buffers;
SHOW work_mem;
SHOW maintenance_work_mem;
SHOW effective_cache_size;

-- Check SSD settings
SHOW random_page_cost;
SHOW effective_io_concurrency;
```

## CrunchyBridge-Specific Notes

1. **Restart Required**: Some settings require a database restart. CrunchyBridge handles this automatically with minimal downtime.

2. **Connection Pooling**: CrunchyBridge includes built-in connection pooling. You may not need separate PgBouncer.

3. **Auto-tuning**: CrunchyBridge has some auto-tuning, but explicit FTS optimization settings override defaults.

4. **Monitoring**: Use CrunchyBridge's built-in monitoring dashboard to track query performance after changes.

## Troubleshooting

### Settings Not Taking Effect

If settings don't apply:
1. Check for typos in parameter names
2. Verify you have admin permissions
3. Wait for automatic restart to complete (~30 seconds)
4. Check logs in CrunchyBridge console

### "Parameter Cannot Be Changed"

Some parameters may be locked by CrunchyBridge. Contact support if:
- `jit` cannot be disabled
- Memory settings rejected

### Performance Not Improved

If performance doesn't improve after configuration:
1. Run `ANALYZE` to update statistics: `ANALYZE meetings_meetingpage;`
2. Verify GIN indexes rebuilt with fastupdate=off (migration 0008)
3. Check `pg_stat_statements` for actual query times
4. Proceed to Phase 1.3 (Redis caching)

## Next Steps

After applying these settings:
1. Run migrations to optimize GIN indexes: `python manage.py migrate`
2. Restart application to pick up Django settings changes
3. Monitor query performance using pg_stat_statements
4. Proceed to Phase 1.3: Redis query caching
