# Quickwit + Fastly Object Storage Migration Plan

**Date**: 2026-04-28  
**Goal**: Replace PostgreSQL FTS with Quickwit search on Fastly S3-compatible storage  
**Status**: Infrastructure ready, DNS issue in dev environment (works in production)

---

## Architecture

```
PostgreSQL (MeetingPage) → Quickwit indexer → Fastly Object Storage (index splits)
                                      ↓
                               Quickwit searcher
                                      ↓
                            Django QuickwitBackend → Web UI
```

**Why this works for civic-observer:**
- Quickwit stores its index on S3-compatible storage (your Fastly bucket)
- The search service is lightweight and stateless
- 12M pages ≈ 10-50GB index on S3
- ~$0.23-$1.15/month on Fastly storage
- Sub-100ms query times (vs 5+ seconds on PostgreSQL FTS)

---

## What's Done

### Files Created
| File | Purpose |
|------|---------|
| `searches/search_backends.py` | Added `QuickwitBackend` class |
| `searches/quickwit_client.py` | HTTP client for Quickwit REST API |
| `searches/management/commands/configure_quickwit.py` | Create index command |
| `searches/management/commands/index_meeting_pages_quickwit.py` | Bulk indexing command |
| `quickwit/index-config.yaml` | Quickwit index schema |
| `quickwit/quickwit-config.yaml` | Quickwit node configuration |
| `docker-compose.yml` | Quickwit service added |
| `.env` | Quickwit config variables |
| `.env-dist` | Template with placeholders |
| `public-works/storage/quickwit_bucket.py` | S3 bucket management module |
| `public-works/scripts/create_quickwit_bucket.py` | CLI script for bucket lifecycle |

### Existing Files Updated
| File | Changes |
|------|---------|
| `config/settings/base.py` | Added `QUICKWIT_URL`, `QUICKWIT_INDEX_ID`, `QUICKWIT_TIMEOUT` |
| `docker-compose.yml` | Added `quickwit` service with healthcheck and depends_on |

---

## Fastly Object Storage Setup

### ✅ Bucket Created
- **Name**: `quickwit-civic-observer`
- **Region**: `us-west`
- **Endpoint**: `https://us-west.object.fastlystorage.app`
- **Credentials**: Set in `public-works/.env`

### Management Commands
```bash
# Check bucket status
cd public-works && uv run python scripts/create_quickwit_bucket.py --info

# List contents
cd public-works && uv run python scripts/create_quickwit_bucket.py --list

# Upload index config
cd public-works && uv run python scripts/create_quickwit_bucket.py \
  --upload-config --bucket quickwit-civic-observer
```

---

## Local Development (File Metastore)

The Quickwit Rust AWS SDK cannot resolve `us-west.object.fastlystorage.app` from inside the Docker container on this development machine. This is a DNS resolution issue specific to the dev environment.

**Workaround**: Use file-backed metastore for development:

```bash
# Already configured in docker-compose.yml for development:
QW_METASTORE_URI=file:///quickwit/data/metastore
QW_DEFAULT_INDEX_ROOT_URI=file:///quickwit/data/indexes
```

The index data stores locally in the `quickwit-data` Docker volume during development, and switches to S3 automatically in production.

---

## Production Deployment (S3 Metastore)

Once deployed to production (where the Fastly hostname resolves correctly), update `docker-compose.yml`:

```yaml
quickwit:
  environment:
    - QW_METASTORE_URI=s3://quickwit-civic-observer/metastore
    - QW_DEFAULT_INDEX_ROOT_URI=s3://quickwit-civic-observer/indexes
    - QW_S3_ENDPOINT=https://us-west.object.fastlystorage.app
    - AWS_ACCESS_KEY_ID=${FASTLY_ACCESS_KEY_ID}
    - AWS_SECRET_ACCESS_KEY=${FASTLY_SECRET_ACCESS_KEY}
    - AWS_DEFAULT_REGION=${FASTLY_REGION:-us-west}
```

---

## Step-by-Step Usage

### 1. Start Services
```bash
docker compose up -d db redis quickwit
```

### 2. Create Index
```bash
docker compose exec quickwit quickwit index create \
  --index-config /opt/quickwit/index-config.yaml
```

### 3. Index Data from PostgreSQL
```bash
# Small batch for testing (10 pages)
docker compose exec web python manage.py index_meeting_pages_quickwit \
  --limit 10

# Full index
docker compose exec web python manage.py index_meeting_pages_quickwit

# Specific municipality
docker compose exec web python manage.py index_meeting_pages_quickwit \
  --municipality alameda-ca
```

### 4. Enable Quickwit Backend
Set in `.env`:
```env
SEARCH_BACKEND=quickwit
QUICKWIT_URL=http://quickwit:7280/api/v1
QUICKWIT_INDEX_ID=meeting_pages
```

### 5. Test Search
```bash
curl http://localhost:8888/search/?q=police
```

---

## Quickwit Document Schema

| Field | Type | Purpose |
|-------|------|---------|
| `id` | text (raw) | Primary key (civic.band page ID) |
| `page_number` | u64 | Page number within document |
| `text` | text (default tokenizer, position index) | **Primary search field** — page content |
| `meeting_name` | text (default tokenizer, position index) | Meeting body name (searchable) |
| `meeting_date` | datetime (rfc3339) | For sorting/filtering |
| `document_type` | text (raw) | "agenda" or "minutes" (filter) |
| `municipality_id` | text (raw) | Municipality UUID (filter) |
| `municipality_subdomain` | text (raw) | e.g., "alameda-ca" (filter) |
| `municipality_name` | text (default tokenizer) | Municipality name (searchable + filter) |
| `state` | text (raw) | State/province abbreviation (filter) |
| `document_id` | text (raw) | MeetingDocument UUID (filter) |
| `page_image` | text (raw) | Image URL path (returned, not stored in index) |

---

## Performance Expectations

| Metric | PostgreSQL FTS | Quickwit |
|--------|---------------|----------|
| Search latency (12M docs) | 5+ seconds | <100ms |
| Count query | Full scan | Instant |
| Pagination | Slow OFFSET | Fast offset |
| Index storage | ~400GB in PostgreSQL | ~10-50GB on Fastly S3 |
| Storage cost | Included in DB cost | ~$0.23-$1.15/month |
| Query scalability | Limited by PostgreSQL | Scales independently |

---

## Troubleshooting

### Quickwit won't start
Check DNS resolution:
```bash
docker run --rm alpine ping -c 1 us-west.object.fastlystorage.app
```

### Index creation fails
```bash
docker compose exec quickwit quickwit index list
docker compose exec quickwit quickwit index delete --index-id meeting_pages --yes
docker compose exec quickwit quickwit index create --index-config /opt/quickwit/index-config.yaml
```

### Search returns no results
1. Check index exists: `curl http://localhost:7280/api/v1/indexes/meeting_pages`
2. Check document count: `curl http://localhost:7280/api/v1/indexes/meeting_pages`
3. Re-index if needed: `python manage.py index_meeting_pages_quickwit`

### Bucket issues
```bash
# List bucket contents
aws s3 ls s3://quickwit-civic-observer/ \
  --endpoint-url https://us-west.object.fastlystorage.app \
  --region us-west \
  --no-sign-request
```

---

## Backfill on Webhook

The existing webhook-triggered backfill (`meetings/tasks.py`) should be updated to also push new pages to Quickwit after PostgreSQL insert. Add a Quickwit indexing step to `meetings/resilient_backfill.py::ResilientBackfillService.process_page_batch()`.

Pattern:
```python
# After bulk_create in PostgreSQL
if settings.SEARCH_BACKEND == 'quickwit':
    documents = [page_to_meilisearch_document(page) for page in batch]
    ingest_documents(documents, input_format='json')
```

---

## Next Steps

1. ✅ Create Fastly bucket (`quickwit-civic-observer`)
2. ✅ Add Quickwit service to docker-compose
3. ✅ Create index config and schema
4. ✅ Build Django QuickwitBackend
5. ✅ Add management commands for indexing
6. ⚠️ Fix DNS resolution in dev (use file metastore for now)
7. 🔄 Index all 12M pages into Quickwit
8. 🔄 Test search performance
9. 🔄 Switch SEARCH_BACKEND to quickwit
10. 🔄 Update backfill tasks to index into Quickwit
