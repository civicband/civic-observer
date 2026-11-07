# Research benchmark (Gate A)

Run `python manage.py research_benchmark --limit 100 --with-insert` against a
production-sized database copy and paste the table here before setting or
raising `RESEARCH_SET_MAX_ITEMS`.

Open item from the design: confirm the out-of-band BM25 index actually covers
`modified` and the `build_where` filter columns. If it does not, the
`modified_after` search and filtered searches will fall back to a sequential
scan; add the column to the index or a concurrent btree before shipping sets.
