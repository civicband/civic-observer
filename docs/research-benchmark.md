# Research benchmark (Gate A)

Run `python manage.py research_benchmark --limit 100 --with-insert` against a
production-sized database copy and paste the table here before setting or
raising `RESEARCH_SET_MAX_ITEMS`.

## 2026-11-08 run

Database: `cb-db-1` production-sized corpus (14,672,983 `meetings_meetingpage`
rows), `pg_search` installed. Command run from the host with `DATABASE_URL`
pointed at that database. The research tables do not exist there yet, so the
insert case writes to a temporary table.

```
case                        exec_ms  plan_rows      hit     read
housing_ca                  1176.66        100    17549    14141
housing_nationwide           249.68        100    21043     6269
five_word_phrase            1201.09          1     2816    11129
housing_ca_modified          763.36        100   232925      267
housing_ca_insert           5400.41        0.0   213683      102
```

Match counts: `housing` in California = 373,129; nationwide `housing` exceeds
the 500,000 cap (TOO_LARGE). Insert case = 373,129 rows into a temp table with
the production unique index in 5.4s. The provisional `RESEARCH_SET_MAX_ITEMS`
of 500,000 is unchanged; nothing here argues for raising it.

### Finding 1: filters must be pushed into the BM25 query

`build_where` originally emitted separate SQL predicates for `state`,
`municipality_id`, `document_type` and `meeting_date`. Those are not pushed
into the BM25 index scan. Measured against the production-sized corpus:

| Query | SQL filters | BM25 field filters |
| --- | --- | --- |
| `housing` in CA count | ~20s (cold), 4–6s warm | 0.44s cold, ~0.7s warm |
| `housing` in CA + OR | 16.8s | 0.39s |
| `housing` + CA + agenda + 2024 | 20.1s | 0.35s |

`build_where` now folds indexed filters into one `paradedb.parse` query for
text/meeting-name searches. Filter-only queries keep plain SQL predicates so
environments without `pg_search` (the test database) still work. Verified
parity on states (including the `OR` state code, which must be quoted),
municipality UUIDs, document type and date ranges with one or both bounds.

### Finding 2: `modified` is not in the BM25 index

`meetingpage_bm25_idx` covers `id`, `text`, `meeting_name`,
`municipality_id`, `municipality_subdomain`, `state`, `document_type`,
`meeting_date`, `document_id` and `page_number`. It does **not** include
`modified`, so `modified_after` stays a SQL predicate. A 30-day "housing in CA"
query filters 373,129 BM25 matches down to 17,339 in 0.76s; acceptable, but a
modified-only alert scan still falls back to the table. Add `modified` to the
index config or a concurrent btree before relying on large modified-only
scans.

### Finding 3: scored insert needs the unqualified score form

`INSERT ... SELECT ... paradedb.score(mp.id) ... ON CONFLICT DO NOTHING` fails
with `Unsupported query shape` on the installed pg_search. The unqualified
`paradedb.score(id)` form works and inserts 373,129 rows in 10.3s. The
materializer and new-matches paths use the unqualified form for text queries
and store `rank NULL` for facet-only queries (there is no BM25 predicate to
score).

### Known risk: re-upserts re-report unchanged pages

`MeetingPage.modified` advances on every backfill re-upsert because
`meetings/services.py` writes `modified` for every fetched row. Alert cutoffs
therefore use `modified` (so genuinely re-OCR'd pages count as new) but a
backfill can re-report unchanged pages. Research sets dedupe with `ON CONFLICT`;
saved searches do not yet. The future mitigation is a conditional upsert that
leaves `modified` untouched when the content hash is unchanged, or dedupe in the
saved-search notification path.
