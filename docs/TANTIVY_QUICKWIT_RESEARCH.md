# Tantivy / Quickwit Research for Full-Text Search

**Date**: 2026-04-10
**Context**: Evaluating alternatives to PostgreSQL FTS for civic-observer search (12M+ rows now, targeting 100M)

---

## Why We're Looking

PostgreSQL FTS is returning 5s+ query times on the `meetings_meetingpage` table (12M+ rows) despite:
- GIN indexes with `fastupdate=off`
- Removal of `ts_rank()` computation
- Redis caching layer (5-min TTL)
- Autovacuum tuning
- JIT disabled, SSD-tuned planner settings

Meilisearch was previously evaluated and abandoned — index size was prohibitive at 12M docs and loading was too slow.

---

## Tantivy (Rust FTS Library)

### What It Is
A Rust full-text search library, similar to Apache Lucene but lighter. Can be used as an embedded library (via `tantivy-py` Python bindings) or as the engine behind higher-level search servers.

### Scaling Characteristics

| Metric | Value | Source |
|--------|-------|--------|
| Theoretical doc limit | ~4.29B per segment (`u32` DocId) | Tantivy source code |
| Proven at scale | 172M documents | [GitHub Issue #2316](https://github.com/quickwit-oss/tantivy/issues/2316) |
| Query latency (172M, hot cache) | ~80ms default, ~35ms with THP, **~2ms with SSTable term dict** | Same issue |
| Indexing throughput | ~12,500 docs/sec (single node Python) | tantivy-py benchmarks |
| Indexing throughput (distributed) | ~200,000 docs/sec (10 nodes with Ray) | tantivy-py benchmarks |

### Critical Finding: SSTable Term Dictionary

At 100M+ documents, the default FST-based term dictionary becomes a bottleneck (~80ms lookups). Switching to the **SSTable term dictionary** (enabled via the `quickwit` feature flag) drops query latency to ~2ms. This is essential for large-scale deployment.

### Memory Model
- Uses memory-mapped files for search (OS page cache is the primary memory consumer)
- Indexing memory is controlled by `overall_heap_size_in_bytes`, minimum 15MB
- Can index datasets of any size on modest hardware — segments flush to disk
- Performance degrades when index exceeds available RAM (page cache thrashing)

### Disk Estimates for 100M Documents
Extrapolating from Quickwit split sizes (10M docs = 1-10GB):
- **100M documents ≈ 10-100GB disk** depending on document size and indexed fields

---

## tantivy-py (Python Bindings)

### Current State (v0.25.1, December 2025)

| Aspect | Assessment |
|--------|-----------|
| PyPI downloads | ~424,000/month |
| Open issues | 12 |
| Python versions | 3.9 - 3.14 |
| GIL handling | Releases GIL during index building and searcher acquisition |
| Maintenance | Active, regular releases |
| Contributors | ~10 (small team) |

### Known Gaps vs Upstream Tantivy
- No ranking by string fast field
- No unbounded range queries
- Limited fuzzy query support with JSON fields
- No field boost API
- No custom tokenizer registration
- API mirrors Rust too closely (not always Pythonic)
- Sparse documentation

### Assessment for civic-observer
**Usable for current 12M scale but risky for 100M.** The small maintainer team, missing features, and need to manage index files / concurrent access / update logic yourself makes this a significant operational burden. Better suited as a prototype path than a production path at scale.

---

## Quickwit (Distributed Search on Tantivy)

### What It Is
A distributed search engine built on Tantivy with compute-storage separation. Designed for append-heavy workloads (logs, events, documents). Runs as a Docker service.

### Why It's a Strong Fit for civic-observer
1. **Append-optimized**: Meeting data is almost entirely inserts, rarely updates — exactly Quickwit's sweet spot
2. **Proven scale**: Used in production at 50 trillion documents / 40 PB uncompressed
3. **100M is trivial**: Would fit in ~10 splits (10M docs each), runnable on a single node
4. **Docker-native**: Fits right into our existing docker-compose setup
5. **Cost-effective**: Object storage (S3/MinIO) for index data, stateless searchers

### Production-Proven Scale

| Metric | Value |
|--------|-------|
| Largest known cluster | ~40 PB uncompressed logs |
| Document count (largest) | 50 trillion (5×10¹³) |
| Index storage on S3 | 7.5 PB |
| Indexing throughput (largest) | 1 PB/day (14M docs/sec, 200 pods) |
| Indexing throughput (14-pod) | 540 MB/s (~46 TB/day) |
| Split open time on S3 | 60ms (with hotcache) |

### Resource Requirements for 100M Documents

| Component | CPU | RAM | Disk |
|-----------|-----|-----|------|
| Indexer | 7.5 MB/s per core | 4-8 GB per core | 120GB+ SSD |
| Searcher | scales with QPS | 4-8 GB per core | Optional (stateless) |
| Control Plane | 1 core | 2 GB | None |
| Metastore | 1-2 cores | 2-4 GB | None |
| **Single node minimum** | **2 cores** | **8 GB** | **SSD** |

**Estimated cost**: S3 storage for index ~$5/mo, small instance ~$30-50/mo. Well within budget.

### Trade-offs vs Elasticsearch
- 5x cheaper compute, 2x cheaper storage in reported migrations
- Optimized for append-only data
- Lower QPS than Elasticsearch for the same hardware
- Higher query latency (~60ms split open on S3 vs sub-ms for local ES)
- **No real-time updates** — batch indexing model (acceptable for meeting data which arrives in batches via webhooks)

### Trade-offs vs Typesense
- Typesense caps out around 28M docs (largest public deployment); no native sharding
- Typesense requires entire index in RAM (~50-150GB for 100M docs)
- Quickwit uses disk/object storage — dramatically cheaper at scale
- Quickwit has higher per-query latency but handles much larger datasets

### Integration Path
We already have a `SearchBackend` abstraction (`searches/search_backends.py`) with `PostgresSearchBackend` and `MeilisearchBackend`. Adding a `QuickwitBackend` would follow the same pattern:
- Quickwit has an Elasticsearch-compatible REST API
- Filter expressions map to our existing filter patterns (municipality, state, date range, document type)
- Sort by `meeting_date` descending (our current ordering)

### Indexing Strategy
Quickwit is designed for batch indexing. Our webhook-triggered backfill jobs already process meeting data in batches — we'd add a step to push documents to Quickwit after PostgreSQL insert. The existing `searches/indexing.py` (built for Meilisearch) provides a template for the batch document format.

### Quickwit vs Manticore Search
On a 100M Hacker News comments benchmark, Manticore Search was 2.86x faster than Quickwit. Manticore is worth noting as another option but has a smaller ecosystem and less mature cloud/object-storage integration.

---

## Recommendation

**Quickwit is the clear winner for scaling to 100M documents.** It's the only option in this evaluation that is:
1. Proven at scales far beyond our target
2. Cost-effective on modest hardware
3. Architecturally aligned with our append-heavy workload
4. Deployable via Docker alongside our existing stack
5. Integrable via our existing `SearchBackend` abstraction

**However**: Before investing in Quickwit, we should first diagnose why PostgreSQL FTS is returning 5s queries. The existing optimizations (GIN indexes, no rank, Redis cache) should yield sub-second performance at 12M rows. Something else may be wrong (query plans, table bloat, misconfigured memory on production). A quick diagnosis could save significant effort.

---

## References

- [Tantivy GitHub](https://github.com/quickwit-oss/tantivy)
- [Tantivy Issue #2316 - 172M doc performance](https://github.com/quickwit-oss/tantivy/issues/2316)
- [tantivy-py on PyPI](https://pypi.org/project/tantivy/)
- [Quickwit documentation](https://quickwit.io/docs/)
- [Quickwit cluster sizing](https://quickwit.io/docs/main-branch/deployment/cluster-sizing)
- [Quickwit 0.8 announcement](https://quickwit.io/blog/quickwit-0.8)
- [Manticore vs Quickwit benchmark](https://manticoresearch.com/comparison/vs-quickwit/)
- [Typesense system requirements](https://typesense.org/docs/guide/system-requirements.html)
- [Typesense sharding issue #295](https://github.com/typesense/typesense/issues/295)
