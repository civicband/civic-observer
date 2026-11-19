# CivicBand Watch: Eng Plan

Nov 19, 2026 · @Philip

## Summary

Build a small staff-only Django app inside civic-observer that ingests the f5bot JSON feed hourly, resolves each thread to a US place, looks up matching CivicBand records, and puts it all in a review queue. It is a power tool: it ranks and gathers context, and the operator (a staff user) decides and posts by hand.

Two outputs from one pipeline:

- **Reply queue**: threads about a place CivicBand covers, with the matching agenda or minutes pages attached.
- **Expansion backlog**: places CivicBand does not cover, ranked by how much people talk about them.

Non-goals: auto-posting, drafting replies, calling Reddit directly.

## Handoff to the implementing agent

This plan fixes what the tool must do and the constraints it must respect; code-level choices belong to the implementer. Read it alongside civic-observer's `CLAUDE.md`.

**Repo and conventions.** civic-observer, `main` at `e240ae9` (2026-11-16). Follow `CLAUDE.md`: uv, `just`, Postgres 17 with ParadeDB in Docker, pytest with tests under `tests/<app>/` and shared factories in `tests/factories.py`, ruff, mypy and pre-commit. Save this plan as `docs/superpowers/specs/2026-11-19-civicband-watch-design.md` and write one implementation plan per milestone under `docs/superpowers/plans/`, as the repo already does.

**Binding (ask the operator before deviating):**

- The decisions table near the end of this plan.
- Staff-only. Nothing posts to Reddit or anywhere else, and nothing calls the Reddit API; the f5bot JSON feed is the only source.
- The feed URL contains an access hash. Read it from an environment variable (for example `F5BOT_FEED_URL`, through environs) and never commit it.
- Migrations only add tables. Do not alter `meetings` or `municipalities` tables or the out-of-band BM25 index, and search through the existing backend rather than raw SQL on `meetings_meetingpage`.
- No new infrastructure: the existing Postgres, Redis, django-rq worker and cron.
- LLM code paths are optional and off by default; the full test suite passes with them disabled.
- Every place resolves to a gazetteer GEOID. Nothing creates a place from free text.
- civic-observer is a public repository. Test fixtures must not contain real Reddit usernames or comment text: keep the feed's structure and replace usernames and snippets with synthetic text.

**Implementer's call:** app name, model and field names, module layout, the string-matching approach, queue scoring weights, templates and styling, how external datasets are downloaded and cached, and cron versus a scheduler.

**Inputs not in the repo** (attached to the planning conversation; the operator supplies them):

- `f5bot_ingest.py` and `test_ingest.py`: the stdlib parser and its 8 tests. Port the parsing functions and tests; drop the SQLite storage code.
- `subreddits.csv`: 63 hand-labelled subreddits; the manual-override seed and the classifier test set.
- One saved feed page (88 items): the source for an anonymized ingest fixture.

**External data** (Census Gazetteer, Census of Governments, GeoNames, Wikidata) is fetched by a management command at load time and never committed; tests use small hand-made fixtures. The Census of Governments link in Sources is a summary report; the per-unit list is a separate download to locate on census.gov.

**When unsure**, take the smaller change and note it in the milestone plan. Stop and ask only when a binding item is in the way.

## Where it lives

Build it as a new app in civic-observer. That repo already has every CivicBand page in Postgres behind a BM25 index, a municipality table, a background worker and staff-only htmx views, so the CivicBand lookup becomes a local query.

|  | civic-observer | corkboard |
| --- | --- | --- |
| Meeting text | `MeetingPage`, about 15.5M rows, ParadeDB BM25 via `get_search_backend()` | One SQLite file per site, read through Datasette |
| Municipalities | `municipalities.Muni` (subdomain, name, state, kind, lat/long, pages), kept current by webhook | `pages.Site`, an unmanaged read-only view of clerk's `sites` table, with pipeline state |
| Background jobs | django-rq worker and Redis; cron runs management commands | None for app work |
| Staff UI | Django admin, neapolitan views, htmx partials, Tailwind, `staff_member_required` | Public front door for civic.band |
| Fit | Existing patterns cover ingest, jobs, search and review screens | Would mean adding tables, workers and private UI to the public site |

corkboard is out of scope. Decision 9 counts every `Muni` civic-observer knows about as covered, so clerk's pipeline state (`current_stage`, only in corkboard) is not needed.

## Starting point

A working stdlib ingester and a hand-built subreddit table already exist and were run on one real feed page (88 alerts, 12.5 hours).

- `f5bot_ingest.py`: polls the feed, follows `next_url` until it reaches a stored id, parses subreddit / post / comment / keyword / title / snippet, writes SQLite. 8 passing tests.
- `subreddits.csv`: 63 subreddits labelled by hand as place, region, state, non\_us or general.
- Findings from the sample: 88 alerts collapse to 69 threads (one r/LosAngeles thread had 16); 29 threads came from local subs; 12 alerts were title-only matches with no body; bot cross-posts put one news video in 2 to 3 subs; snippets run up to 800 characters.

The plan ports the parsing functions into a Django management command unchanged, and turns the CSV into the manual-override layer of the gazetteer classifier.

## Architecture

A new staff-only app inside civic-observer: a cron-triggered ingest command, django-rq jobs for enrichment, and in-process search over the meeting pages already in Postgres. Nothing posts anywhere.

&#91;embedded content: architecture · ingest, enrich, review\]

The left column runs top to bottom once per new alert. Corrections the operator makes in the triage UI flow back into the subreddit table and the place aliases, so classification improves with use.

## Data model

The new app's tables sit in civic-observer's Postgres and point at the existing `municipalities.Muni` and `meetings.MeetingPage` rather than copying CivicBand data. The thread is the unit of triage; mentions hang off it, and place resolution happens at both levels. The fields below are a starting point; names, types and indexes are the implementer's call.

| Model | Key fields | Notes |
| --- | --- | --- |
| `Mention` | f5bot id (PK), thread FK, url, kind, keyword, author, snippet\_text, snippet\_html, published\_at, raw JSON | Immutable once stored. |
| `Thread` | key (`reddit:<sub>/<post_id>`), subreddit FK, title, first\_seen, last\_seen, mention\_count, status, snoozed\_until, notes | All triage state lives here. |
| `ThreadPlace` | thread FK, place FK, role (subject or mentioned), confidence, source (subreddit, post, comment, manual) | Many per thread. The rollup of `PlaceMention` rows; the operator's promotions and corrections set `source` to manual and are never overwritten. |
| `Subreddit` | name (PK), kind, place FK, state, confidence, source, muted, reviewed\_at | `source` = manual, wikidata, gazetteer, text\_evidence or llm. |
| `Place` | GEOID (PK), name, type (city, town, village, CDP, county, township, school district), state, county FK, population, is\_government, aliases, muni FK (nullable) | Loaded from the gazetteer. A non-null `muni` means CivicBand covers it. |
| `PlaceMention` | mention FK, place FK, matched text, method, confidence, is\_subject | Raw extraction output; many per mention. |
| `MeetingHit` | thread FK, place FK, page FK to `MeetingPage`, query, rank, snippet | Cached search results per subject place; meeting name, date and link come from the page. |
| `IngestRun` | started\_at, pages, seen, new, error | Health and monitoring. |

The backlog is a query, not a table: places with no `muni`, ranked by distinct threads where it is a subject place, and distinct subreddits, in the last 30 days. A small `PlaceNote` model (place FK, text, status) holds the operator's notes on candidates. Migrations stay small and additive; nothing touches the existing meetings tables or the out-of-band BM25 index.

## Scheduled ingestion

Follow civic-observer's existing pattern: cron runs a management command (as it already does for `send_daily_digests`), and the work runs as django-rq jobs on the existing `default` worker. `manage.py ingest_f5bot` runs hourly and enqueues one `enrich_thread` job per new or changed thread. One feed page covered 12.5 hours, so hourly polling never falls behind, and the stop-at-a-stored-id rule means no cursor needs saving.

- **Backfill**: run once with a high `--max-pages` to pull whatever history the f5bot plan keeps.
- **Overlap**: take a Redis lock (Redis is already there for django-rq) so two runs never interleave.
- **Failures**: write every run to `IngestRun`, including the error. The admin index shows the last successful run; if it is more than 6 hours old, or a day passes with zero new alerts, send an email.
- **Dedupe at ingest**: group bot cross-posts by normalized title within 48 hours into one story, and collapse identical text from the same author in one thread (the r/bullcity double post).
- **Enrich step**: attach the thread, apply the subreddit classification, run place extraction, refresh CivicBand hits for each covered subject place. Each step is idempotent and can be re-run per thread from the admin.

This adds no infrastructure. About 175 alerts a day is negligible next to the meeting backfills the worker already runs, and failure emails can go through the existing Postmark setup.

## Subreddit categorization via gazetteer

Build the `Place` table from Census files keyed on GEOID, add alternate names from GeoNames and Wikidata, then classify each new subreddit by layered lookups. Every classification is a suggestion with a method and a confidence; the operator confirms it once in the admin.

### Sources

| Source | What it gives | Role |
| --- | --- | --- |
| [Census Gazetteer Files](https://www.census.gov/geographies/reference-files/time-series/geo/gazetteer-files.2026.html) (places, county subdivisions, counties, school districts) | GEOID, name, type, lat/long; tab-delimited, yearly | Spine of `Place`. Population is not included, so join a Census population estimate. |
| [Census of Governments](https://cdn.www.census.gov/content/dam/Census/library/publications/2025/econ/govtorg2225.pdf) | 91,438 local government units in 2025 | Sets `is_government`. Separates real cities and townships from CDPs, which have no council and roll up to the county. |
| [USGS GNIS](https://www.usgs.gov/us-board-on-geographic-names/download-gnis-data) | Official names with Census codes; variant names listed as coming soon | Cross-check names to GEOIDs; variants when published. |
| [GeoNames](https://geonames.org/export/) | Alternate names; CC-BY, commercial use allowed | Aliases, plus the world cities file to flag non-US subreddits. |
| Wikidata | A subreddit property on place items, and nicknames | Direct subreddit-to-place links (r/bullcity to Durham); one SPARQL export. Believed to be P3984; verify before use. |
| `subreddits.csv` | Hand labels | Manual overrides; always wins. Also the test set. |

### Classifier order

The first layer that answers wins:

1. Manual override from the CSV or the admin.
2. Wikidata exact subreddit match.
3. Exact name key: normalize the subreddit (lowercase, strip non-letters) and match against keys generated per place: `name`, `name+state abbr`, `name+state name`, `name+city`, `name+county`. Catches chicoca, lacrossewi, fountaincolorado, cecilcounty.
4. Affix stripping: remove known prefixes and suffixes (development, daily, news, talk, landlords, ask, r4r, jobs) and retry at lower confidence. Catches DevelopmentSLC only with an alias for SLC, which is why aliases matter.
5. Ambiguity: if several places share the key, pick the most populous only when it is at least 10 times the next; otherwise mark ambiguous (Glendale CA vs AZ).
6. States and regions: state names plus a short manual list (bayarea, socal, pnw, dmv).
7. Non-US: a match against GeoNames cities over 15,000 people outside the US with no US place of similar size.
8. Otherwise general.

Once a few weeks of extraction data exist, a nightly job adds text evidence: if one place accounts for most resolved mentions in a subreddit, it suggests that place (or flags a disagreement with the existing label). Success measure: the classifier reproduces the 63 hand labels, with ambiguous ones flagged rather than wrong.

Link each `Muni` to its `Place` once, by name and state with lat/long to break ties, and review the leftovers in the admin. That link is what marks a place as covered, and it lets the backlog skip places CivicBand already has.

## Place extraction

Steps 1 to 3 and 5 are deterministic and must be enough on their own; step 4 (LLM) is optional and off by default. Every name resolves to a GEOID through the gazetteer. Thresholds follow decisions 1 to 4: favor recall and badge low confidence.

Inputs per mention: post title, snippet (up to 800 characters), the subreddit's classification, and the matched keyword.

1. **Body-pattern rules.** Regexes for the phrases the keywords imply: "{Name} City Council", "{Name} County Board of Supervisors", "{Name} Planning Commission", "Town of {Name}". These give a place and a body type together (Beverly Hills Planning Commission, Riverside County Board of Supervisors).
2. **Gazetteer phrase match.** Match capitalized place names and aliases against `Place`, using the subreddit's state as a prior to break ties. Accept unanchored matches at low confidence, shown with a badge, except names that are also common words (Mobile, Independence, Bell).
3. **Body-type rollup.** "Board of supervisors" resolves to the county; a CDP resolves to its county, since CDPs have no council.
4. **LLM pass, optional and off by default.** Runs when the subreddit is general, region or state; when steps 1 and 2 disagree with the subreddit; or when a match is ambiguous. A small model returns JSON: places (name, state, role = subject or mentioned), whether it is US, and a short topic phrase to reuse as the CivicBand search query. At an estimated 40% of about 175 daily alerts, that is around 70 calls a day.
5. **Thread rollup.** Each place found becomes a ThreadPlace row. The subreddit's place and places in the post or title are subjects; a comment's place is a subject only when a body pattern names it, otherwise mentioned. So the Huntsville comment in the McKinney recall thread adds Huntsville as a subject, while the New York comparisons in r/LosAngeles stay mentioned.

Evaluation: the operator labels about 200 threads in the first two weeks (decision 8) with the correct places and a useful or not flag. A report command computes precision and recall per method against those labels, to show which layer earns its keep.

## CivicBand lookup

Inside civic-observer the lookup is an in-process query: run the thread's topic through the existing ParadeDB BM25 backend, scoped to each covered subject place's `Muni`. No HTTP round-trip, no bot protection, and the results match what the public search shows.

- **Call.** `get_search_backend().search(query_text=topic, municipalities=[muni], date_from=..., limit=5)`, or `search_with_cache` to reuse the existing cache. Each result carries the page id, meeting name, date, document type and a BM25 snippet.
- **Query terms.** Built locally, no LLM: the post title plus capitalized phrases from the snippet, minus stopwords, the f5bot keyword and the place name itself. BM25 already weights rare terms up and common ones down, so the query needn't be precise. The optional LLM phrase can replace it later. Search the last 12 months first and fall back to all dates; agenda pages dated after today get flagged so the operator can snooze a thread until after the meeting.
- **Links.** `MeetingPage.civic_band_url` gives `https://<subdomain>.civic.band/meetings/<table>/<id>` for the copy-citation button; `page_image_thumbnail_url` gives a preview.
- **Cache.** Store hits as `MeetingHit` rows with the query that produced them; refresh when the thread gets new mentions, and re-run live when the operator edits the query in the triage view.

**Coverage.** A place counts as covered when its `Place` links to a `Muni` of any page count (decision 9); a Muni with no pages yet simply returns no hits. `Muni` arrives by webhook, so new CivicBand sites appear without extra work.

Nothing left to confirm on the schema: `MeetingPage` already copies municipality, meeting name, date and document type onto each page for search.

## Triage UI

Django admin covers everything except the per-thread decision screen. That screen gets one custom view built the way civic-observer's existing pages are (neapolitan views, htmx partials, Tailwind), behind `staff_member_required`. Ad-hoc questions go through the admin or `just dbshell`.

**Django admin (most of the work, almost free):**

- `Thread` changelist: filters for status, subreddit kind, covered by CivicBand, US or non-US, keyword and age; search over title and snippets; actions to dismiss, mark replied, snooze, send to backlog, mute the subreddit.
- `Subreddit` review: a "needs review" filter for unconfirmed suggestions, with the method and confidence shown, an autocomplete for `place`, and an accept action for bulk confirmation.
- `Place`: autocomplete-backed editing, its linked `Muni`, notes inline, plus a filter for places whose `Muni` link still needs review.
- `IngestRun`: read-only, newest first, for health checks; the django-rq dashboard at `/django-rq/` shows the jobs.

**Custom queue view (`/triage/`):** one thread at a time, ordered by score (covered place, age under 24 hours, question in the text, number of mentions).

- Left: thread title, subreddit, age, each mention's snippet with the keyword highlighted, a link to open it on Reddit.
- Right: each subject place with its CivicBand hits side by side and an editable query that re-runs in place; mentioned places as labels the operator promotes with one click; any place can be corrected in one click, and a correction also updates the subreddit or adds an alias; a copy-citation button giving a markdown link with meeting name and date.
- Keys (suggested bindings): `j`/`k` next and previous, `r` replied, `d` dismiss, `s` snooze, `b` backlog, `p` promote a mentioned place, `m` mute subreddit, `x` mute country, `e` edit query, `c` copy citation.

**Backlog view (`/backlog/`):** uncovered places ranked by threads where they are a subject place and by distinct subreddits over 30 days, with population, sample threads and the operator's notes.

## Milestones

Six milestones, each shippable on its own and each ending in a check the implementer can run. The admin is a usable queue after milestone 2: in the sample, subreddit place alone resolved 29 of 69 threads. Day estimates are rough.

1. **App skeleton and ingest** (1 to 2 days). New app, models and migrations, an `ingest_f5bot` command around the ported parser, an `enrich_thread` rq job (a no-op stub for now), a cron entry, admin for threads, mentions and ingest runs.
   - Done when: ingesting the anonymized 88-item fixture creates 88 mentions and 69 threads; a second run creates nothing; with two linked fixture pages, polling follows `next_url` and stops at the first stored id; each run is recorded with its counts; the ported parser tests pass.
2. **Gazetteer and subreddit classifier** (2 to 3 days). Loader command for the Census files, government flags, population, GeoNames aliases and Wikidata subreddit links; `Muni`-to-place linking; the layered classifier; review admin.
   - Done when: the loader is idempotent; every `Muni` either links to a place or appears in a review list; with manual overrides switched off, the classifier gives no wrong place for any of the 63 hand-labelled subreddits (ambiguous is allowed, wrong is not).
3. **Meeting lookup** (half a day to 1 day). Local query builder and a call to the existing search backend per covered subject place; results cached.
   - Done when: a thread whose subject place links to a `Muni` gets up to 5 cached hits per place from the existing backend; re-running with the same query adds no duplicates; tests exercise the backend the same way the existing `tests/searches/` tests do.
4. **Triage and backlog views** (2 to 3 days). htmx queue with keyboard actions, copy citation, mutes, backlog ranking.
   - Done when: both views reject non-staff users (tested); every queue action persists; muting a subreddit or a country hides its threads; the backlog lists only places with no `Muni`, ranked by subject-place threads.
5. **Place extraction** (2 to 3 days). Body-pattern rules, gazetteer matcher, body-type rollup, thread rollup into subject and mentioned places, the optional LLM step behind a setting, a labelling admin and a precision/recall report command.
   - Done when: with the LLM off, fixture cases resolve as specified (a "Beverly Hills Planning Commission" snippet in a general subreddit gives Beverly Hills as subject; a council-pattern comment naming another city adds that city as subject; a passing comparison stays mentioned); operator labels can be recorded; the report command prints per-method precision and recall.
6. **Hardening** (ongoing). Cross-post grouping, duplicate collapse, text-evidence suggestions for subreddits, the stale-ingest email, a weekly summary of replied threads.

## Risks

- **f5bot is a single upstream.** A format change or plan limit breaks ingest. Mitigation: raw JSON is stored on every mention, so parsing can be re-run; tests pin the format.
- **Reddit's terms.** Reddit has closed self-service Data API access, and its Responsible Builder Policy forbids commercializing Reddit data without approval. Keep snippets in staff-only views and never republish them.
- **Shared production database.** The app's tables live next to 15.5M meeting pages. Keep migrations additive, never touch the BM25 index, and run search calls with the same limits the public search uses.
- **Gazetteer ambiguity.** Repeated place names (Glendale, Chester County) and nicknames. Mitigation: confidence levels, review in the admin, and text evidence over time.
- **Noise volume.** If f5bot keywords are broadened, general-subreddit noise grows faster than signal. Mitigation: muting and a per-keyword yield report.

## Decisions (binding)

Answered by the operator on 2026-11-19. The rest of the plan follows from these; change them only with the operator.

| # | Question | Decision |
| --- | --- | --- |
| 1 | Precision or recall? | Recall. Show a possibly wrong place and fix it in one click; low-confidence matches carry a badge. |
| 2 | Which jurisdictions? | Anything the gazetteer can name as a jurisdiction without LLM help: counties, incorporated places, county subdivisions with governments, school districts. Special districts wait. |
| 3 | One place per thread, or several? | One queue row per thread; each place on it is tagged subject or mentioned. Subject places get CivicBand hits and count toward backlog demand; mentioned places are labels the operator can promote. |
| 4 | Post or comment as the anchor? | A comment can carry several places. Places from the post or title are subjects; a comment's place is a subject only when a body pattern names it ("Huntsville city council"), otherwise mentioned. |
| 5 | Non-US threads? | Keep them, with one-key mute by subreddit and by country, and a hide-non-US filter. |
| 6 | LLM use? | Optional, off by default. The pipeline must work fully without it. |
| 7 | Topic extraction? | Done locally without an LLM (see the lookup section); LLM topic phrases can come later. |
| 8 | Labelling? | Yes, about 200 threads in the first two weeks. |
| 9 | What counts as covered? | Any `Muni` civic-observer knows about, whatever its pipeline stage. |

Because the gazetteer is US-only, non-US threads resolve to a country (from GeoNames) rather than a place, unless the country is Canada: `Muni` already allows provinces, so Canadian municipalities from GeoNames go into `Place` too.

## Sources

- [Census Gazetteer Files, 2026](https://www.census.gov/geographies/reference-files/time-series/geo/gazetteer-files.2026.html)
- [Census Bureau, local government organization 2025](https://cdn.www.census.gov/content/dam/Census/library/publications/2025/econ/govtorg2225.pdf)
- [USGS, Download GNIS Data](https://www.usgs.gov/us-board-on-geographic-names/download-gnis-data)
- [GeoNames data export](https://geonames.org/export/)
- [Reddit, Responsible Builder Policy](https://support.reddithelp.com/hc/articles/42728983564564)

## Implementation decisions confirmed 2026-11-19

- **App boundary:** new staff-only `watch` app in civic-observer, mounted at `/triage/` and `/backlog/`. Django admin remains the review surface for most models.
- **`Place` identity:** `Place.id` is a UUID. A unique `canonical_key` namespaces the source and geography type (`us:place:0620000`, `us:county:06001`, `us:cousub:0600100000`, `us:school:<geoid>`, `ca:city:<geoname_id>`, `country:IE`). `geoid` is nullable and unique together with `place_type`, never globally. This avoids collisions between 7-digit place and school-district GEOIDs and leaves room for Canada and country rows.
- **Aliases:** normalized aliases live in `PlaceAlias`, not a JSON field, so classification and phrase matching can query indexed keys.
- **Census of Governments:** use the 2022 Government Units Listing (`govt_units_2022.ZIP`), General Purpose and School District sheets. Filter to `IS_ACTIVE = Y`. Special districts and dependent school districts wait. The loader persists `is_government`, population, and best-known county into `Place`; the XLSX is never committed or shipped.
- **XLSX parsing:** add `openpyxl` for the loader only. Tests use small CSV or dict-row fixtures.
- **Canada:** the hand-labelled Canadian `non_us` rows in `subreddits.csv` stay manual `non_us`; they are not auto-promoted. An operator may promote one to a Canadian `Place` in the admin.
- **LLM:** optional and off by default, implemented as a generic OpenAI-compatible HTTP client behind `WATCH_LLM_*` settings using the existing `httpx` dependency.
- **Data files:** raw `alert-json-feed.json` and `Govt_Units_2022_Final.xlsx` stay out of git. The feed contains real usernames/content and its feed URLs contain the f5bot access hash. Only anonymized feed fixtures and small hand-made test fixtures are committed.
- **Meeting hits:** `MeetingHit.page` uses `db_constraint=False` with `DO_NOTHING`, and citation fields are denormalized onto the hit, so the app never adds a constraint to or joins heavily against the 15.5M-row `meetings_meetingpage` table.
- **Cron:** the hourly job is a deploy note for `public-works`; this repo ships the management command only.
