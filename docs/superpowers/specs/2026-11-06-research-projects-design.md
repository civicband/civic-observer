# Research Projects: QDA for civic-observer — Design

Nov 6, 2026 · @Philip

Replace Notebooks with Research Projects: search the whole corpus, freeze the results into a set, then triage, code and filter that set with an audit trail behind every decision.

## Problem and goals

Notebooks let a user save individual pages with a note and flat tags. That works for bookmarking, but not for the job journalists and advocates actually do: start from a broad query ("every mention of housing in California"), cut it down to what matters, and say with confidence what the record shows.

Goals for v1:

- **Search to set.** Any search in the existing UI can be saved as a frozen set, including sets in the hundreds of thousands of pages.
- **Triage.** Each page in a set gets a disposition: unreviewed, relevant or ignored, with an optional ignore reason.
- **Ignore rules.** Project-level rules ignore whole classes of pages (a meeting body, a boilerplate phrase) across every set.
- **Codes.** Project-scoped codes with optional definitions, applied at page level now, with room for text spans and agenda items later.
- **Lenses.** Filters over a set (sub-search, disposition, codes, place, date) are free views; any lens can be frozen into a child set.
- **New matches.** An explicit "check for new matches" action reuses the alerting pipeline.
- **Traceability.** Every query, sub-search, ignore and code change is logged, so a finding can be walked back to the pages and decisions behind it.

The primary user is a journalist or advocate. The data model should not block an academic who later needs a codebook with inclusion and exclusion criteria, memos, and export to NVivo or ATLAS.ti.

## Non-goals for v1

- **Teams.** Projects are owned by one user. The model keeps `project` as the boundary so a membership table can be added later without moving data.
- **AI and semantic search.** No embeddings, no pgvector, no suggested codes.
- **Span and agenda-item coding UI.** The schema carries nullable span fields; v1 codes whole pages only.
- **Intercoder agreement.** Needs more than one coder, so it waits for teams.
- **REFI-QDA export.** v1 exports CSV. The data model maps cleanly to QDPX so it can be added later.
- **Live (smart) sets.** Sets are snapshots. New matches arrive only through an explicit, logged action.

## Concepts

Two axes stay separate throughout: **disposition** (is this page in or out?) is single-valued and per set; **codes** (what is this page about?) are multi-valued and per project. E-discovery tools separate responsiveness from issue tags for the same reason: it makes "relevant pages lacking code X" a trivial query.

| Term | Meaning | Scope | QDA / e-discovery analog |
| --- | --- | --- | --- |
| Project | Container for sets, codes, memos, rules and the audit log | One owner (v1) | NVivo/ATLAS.ti project; review case |
| Query spec | Frozen JSON of a search: text, filters, parser version | Immutable | Stored query |
| Set | Frozen membership produced by a query spec, a lens, or manual adds | Project | Snapshot group; review set |
| Item | One page in one set, with rank and disposition | Set | Document in a review set |
| Disposition | unreviewed, relevant or ignored, plus reason and source | Item | Responsive / non-responsive |
| Ignore rule | A predicate that ignores matching pages in every set | Project | Exclusion criteria; culling rule |
| Code | A label with optional definition, inclusion and exclusion criteria | Project | Code / node |
| Coding | A code applied to a page, optionally to a text span | Project + page | Quotation / reference |
| Memo | Free text attached to the project, a set, a code or a page | Project | Memo / annotation |
| Lens | An unsaved filter over a set; can be frozen into a child set | Request | Smart group / saved search |
| Audit event | Append-only record of a decision | Project | Audit trail entry |

Codings and memos belong to the project, not the set. A page that appears in three sets carries the same codes in all three; only its disposition differs per set.

### Naming: codes in the model, tags in the UI

Models, API fields and exports say `Code`, `Coding` and "codebook"; the UI says "Tags" and "Tag guide". The mismatch is deliberate, so do not rename either side to match the other.

- **Model and export: "code."** It is the term in ATLAS.ti, MAXQDA, Dedoose, current NVivo and the REFI-QDA standard, so an academic exporting to those tools sees familiar vocabulary.
- **UI: "tag."** Journalists and e-discovery users already say "tags". "Code" also collides with the corpus itself: council minutes are full of "municipal code", "building code" and "code enforcement".
- **Bridge:** the Tag guide page carries one help line, "Researchers call these codes; exports use that term."

## Data model

A new `research` app holds eight models. Every reference to a page uses `db_constraint=False, on_delete=DO_NOTHING`, the same pattern `MeetingPage.municipality` already uses. That keeps 400k-row inserts free of per-row FK checks, and research records survive a page being deleted upstream; the UI shows "page no longer in corpus".

```python
class Project(TimeStampedModel):
    id = UUIDField(primary_key=True, default=uuid4)
    owner = ForeignKey(User, CASCADE, related_name="research_projects")
    name = CharField(max_length=200)
    description = TextField(blank=True)
    is_archived = BooleanField(default=False)
    # later: team = ForeignKey("teams.Team", null=True)


class ResultSet(TimeStampedModel):
    class Origin(TextChoices):
        QUERY, LENS, MANUAL, IMPORT

    class Status(TextChoices):
        PENDING, MATERIALIZING, READY, FAILED, TOO_LARGE

    id = UUIDField(primary_key=True, default=uuid4)
    project = ForeignKey(Project, CASCADE, related_name="sets")
    parent = ForeignKey("self", SET_NULL, null=True, related_name="children")
    name = CharField(max_length=200)
    origin = CharField(choices=Origin)
    query = JSONField(
        default=dict
    )  # canonical QuerySpec, or {"parent": id, "lens": {...}}
    query_hash = CharField(max_length=64)  # sha256 of canonical JSON
    status = CharField(choices=Status, default=PENDING)
    item_count = IntegerField(default=0)
    materialized_at = DateTimeField(null=True)  # DB now() when the snapshot ran
    last_checked_at = DateTimeField(null=True)  # last "check for new matches"
    error = TextField(blank=True)


class SetItem(Model):
    class Disposition(TextChoices):
        UNREVIEWED = "u", RELEVANT = "r", IGNORED = "i"

    class AddedVia(TextChoices):
        QUERY, LENS, NEW_MATCHES, MANUAL, IMPORT

    id = BigAutoField(primary_key=True)
    result_set = ForeignKey(ResultSet, CASCADE, related_name="items", db_index=False)
    page = ForeignKey(MeetingPage, DO_NOTHING, db_constraint=False, related_name="+")
    rank = FloatField(null=True)  # BM25 score at materialization
    # denormalized for set-local facets and ordering without touching 15.5M rows
    meeting_date = DateField(null=True)
    municipality_id = UUIDField(null=True)
    state = CharField(max_length=64, blank=True)
    document_type = CharField(max_length=32, blank=True)
    disposition = CharField(max_length=1, choices=Disposition, default="u")
    previous_disposition = CharField(max_length=1, blank=True)  # one level of undo
    disposition_by_rule = ForeignKey("IgnoreRule", SET_NULL, null=True)
    ignore_reason = CharField(max_length=500, blank=True)
    last_event = ForeignKey("AuditEvent", SET_NULL, null=True, db_constraint=False)
    added_via = CharField(choices=AddedVia)
    added_at = DateTimeField(default=now)

    class Meta:
        constraints = [
            UniqueConstraint(fields=["result_set", "page"], name="uniq_item")
        ]
        indexes = [
            Index("result_set", "disposition", F("rank").desc(), "page"),
            Index("result_set", "meeting_date", "page"),
            Index("result_set", "municipality_id"),
        ]


class Code(TimeStampedModel):
    id = UUIDField(primary_key=True, default=uuid4)
    project = ForeignKey(Project, CASCADE, related_name="codes")
    parent = ForeignKey("self", PROTECT, null=True, related_name="children")
    name = CharField(max_length=100)
    color = CharField(max_length=7, blank=True)
    definition = TextField(blank=True)
    inclusion = TextField(blank=True)  # "apply when"
    exclusion = TextField(blank=True)  # "do not apply when" (MacQueen et al.)
    is_archived = BooleanField(default=False)

    class Meta:
        constraints = [
            UniqueConstraint(
                fields=["project", "parent", "name"],
                nulls_distinct=False,
                name="uniq_code",
            )
        ]


class Coding(TimeStampedModel):
    id = UUIDField(primary_key=True, default=uuid4)
    project = ForeignKey(Project, CASCADE, related_name="codings")
    code = ForeignKey(Code, CASCADE, related_name="codings")
    page = ForeignKey(MeetingPage, DO_NOTHING, db_constraint=False, related_name="+")
    span_start = PositiveIntegerField(null=True)  # v1: always null (whole page)
    span_end = PositiveIntegerField(null=True)
    quote = TextField(blank=True)  # text at coding time
    page_hash = CharField(
        max_length=64, blank=True
    )  # MeetingPage.content_hash at coding time
    is_exemplar = BooleanField(default=False)
    coder = ForeignKey(User, SET_NULL, null=True)
    # later: segment = ForeignKey("Segment", null=True) for agenda items

    class Meta:
        constraints = [
            CheckConstraint(
                condition=Q(span_start__isnull=True, span_end__isnull=True)
                | Q(span_start__lt=F("span_end")),
                name="valid_span",
            ),
            UniqueConstraint(
                fields=["code", "page"],
                condition=Q(span_start__isnull=True),
                name="uniq_page_coding",
            ),
            UniqueConstraint(
                fields=["code", "page", "span_start", "span_end"],
                name="uniq_span_coding",
            ),
        ]
        indexes = [Index("project", "page"), Index("code", "page")]


class Memo(TimeStampedModel):
    id = UUIDField(primary_key=True, default=uuid4)
    project = ForeignKey(Project, CASCADE, related_name="memos")
    author = ForeignKey(User, SET_NULL, null=True)
    body = TextField()
    # at most one target; none = project memo
    result_set = ForeignKey(ResultSet, CASCADE, null=True)
    code = ForeignKey(Code, CASCADE, null=True)
    page = ForeignKey(
        MeetingPage, DO_NOTHING, db_constraint=False, null=True, related_name="+"
    )


class IgnoreRule(TimeStampedModel):
    id = UUIDField(primary_key=True, default=uuid4)
    project = ForeignKey(Project, CASCADE, related_name="ignore_rules")
    name = CharField(max_length=200)
    predicate = JSONField()  # same shape as a QuerySpec; all fields ANDed
    reason = CharField(max_length=500, blank=True)
    is_active = BooleanField(default=True)


class AuditEvent(Model):
    id = BigAutoField(primary_key=True)
    project = ForeignKey(Project, CASCADE, related_name="events")
    actor = ForeignKey(User, SET_NULL, null=True)
    verb = CharField(
        max_length=50
    )  # "set.materialized", "items.disposed", "rule.applied", ...
    result_set = ForeignKey(ResultSet, SET_NULL, null=True)
    payload = JSONField(default=dict)  # query/lens spec, counts, before/after values
    created = DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [Index("project", "-created")]
```

Notes on the choices:

- **Query spec on the set, not a separate model.** The existing `Search` is shared, deduplicated and mutated by alerting. A set needs its query frozen and owned, so it stores a canonical JSON copy plus a hash.
- **Denormalized facet columns on `SetItem`.** Facet counts and date ordering over a 400k-item set run on the item table alone. This mirrors the denormalization already on `MeetingPage`.
- **`last_event` on items.** A bulk action ("ignore all 3,100 items in this lens") writes one `AuditEvent`, not 3,100. Items point at the event that last changed them, so the bulk action can be undone.
- **Codings carry `quote` and `page_hash`.** If a re-backfill changes the page text, the coding still shows what the researcher saw, and the UI can flag drift by comparing hashes.

## Set materialization

A set is materialized entirely inside Postgres with one `INSERT ... SELECT`, so 400k page ids never pass through Python. Sets at or under 1,000 matches materialize inline in the request; larger ones run as an RQ job.

### Prerequisite refactor: `QuerySpec` and `build_where`

`PgSearchBackend.search` currently builds its WHERE clause inline. Extract it:

```python
@dataclass(frozen=True)
class QuerySpec:
    text: str = ""
    meeting_name: str = ""
    municipality_ids: tuple[UUID, ...] = ()
    states: tuple[str, ...] = ()
    date_from: date | None = None
    date_to: date | None = None
    document_type: str = "all"
    modified_after: datetime | None = None  # new: for "check for new matches"

    def canonical(self) -> dict: ...  # sorted, normalized; hashed into query_hash
    @classmethod
    def from_search(cls, search: Search) -> "QuerySpec": ...


def build_where(spec: QuerySpec, alias: str = "mp") -> tuple[str, list]: ...
```

The interactive search, alerting, set materialization, ignore rules and lens sub-searches all call `build_where`. That guarantees a set contains exactly what the search page showed, and an ignore rule matches exactly what its preview showed.

### The job

1. Mark the set `MATERIALIZING`.
2. Run a capped count (the existing `_count` pattern, with the cap at `RESEARCH_SET_MAX_ITEMS + 1`). Over the ceiling, mark the set `TOO_LARGE`, store the count, and stop. The UI suggests narrowing by state, body or date.
3. In one transaction:

```sql
INSERT INTO research_setitem
    (result_set_id, page_id, rank, meeting_date, municipality_id, state,
     document_type, disposition, added_via, added_at)
SELECT %(set_id)s, mp.id, paradedb.score(mp.id), mp.meeting_date,
       mp.municipality_id, mp.state, mp.document_type, 'u', 'query', now()
FROM meetings_meetingpage mp
WHERE {where_sql}
ON CONFLICT (result_set_id, page_id) DO NOTHING;
```

4. In the same transaction, apply every active ignore rule (see Ignore rules).
5. Set `item_count`, `materialized_at = now()` (the transaction start, so it is the snapshot cutoff), and status `READY`.
6. Write a `set.materialized` audit event with the canonical spec, item count, ignored-by-rule count per rule, and duration.

`ON CONFLICT DO NOTHING` makes RQ retries safe. Use a dedicated `research` queue so a large materialization never delays alert delivery on `default`, and set a per-job `statement_timeout`.

### Ceiling

Start `RESEARCH_SET_MAX_ITEMS` at 500,000 and tune it after measuring (see Performance). At about 100 bytes a row plus three indexes, a 500k-item set is roughly 100 MB. A user hitting the ceiling sees the count and a narrowing prompt, never a silently truncated set.

### Manual adds

The notebook save button on search results becomes "Add to set": it adds one page to a chosen set with `added_via=MANUAL`. Manual sets (origin `MANUAL`) have no query, so they are skipped by "check for new matches".

## Browsing a set: lenses and sub-search

A lens is a filter over one set, encoded entirely in the URL query string, so every view is bookmarkable and nothing is saved until the user freezes it. Without sub-search text, a lens runs only against `research_setitem`; with text, it adds one BM25 predicate.

### Lens spec

```python
@dataclass(frozen=True)
class Lens:
    text: str = ""  # sub-search, same Tantivy syntax as search
    dispositions: tuple[str, ...] = ("u", "r")  # ignored hidden by default
    codes_all: tuple[UUID, ...] = ()
    codes_any: tuple[UUID, ...] = ()
    codes_none: tuple[UUID, ...] = ()
    uncoded: bool = False
    municipality_ids: tuple[UUID, ...] = ()
    states: tuple[str, ...] = ()
    date_from: date | None = None
    date_to: date | None = None
    document_type: str = "all"
    added_via: tuple[str, ...] = ()  # e.g. only new matches
    order: Literal["rank", "date_desc", "date_asc"] = "rank"

    @classmethod
    def from_querydict(cls, qd) -> "Lens": ...
    def to_querystring(self) -> str: ...
```

### Query paths

- **No text.** Filter `research_setitem` by set, disposition and facet columns; codes become `EXISTS` / `NOT EXISTS` against `research_coding` on `(project_id, page_id)`. Fetch the 50 visible `MeetingPage` rows by primary key afterwards.
- **Text, query-origin set.** AND the sub-search onto the set's own frozen query (`build_where(set_spec) AND text:(sub)`), then semi-join to the set's items. Conjoining narrows the BM25 scan to pages that were already candidates, instead of scoring the sub-term across all 15.5M pages.
- **Text, manual or lens-origin set.** Bare `text:(sub)` BM25 predicate, semi-joined to the items. Slower for broad sub-terms; see Performance.

Unlike the main search, counts inside a set can be exact: the item table is indexed by set, so there is no need for the `COUNT_CAP` sentinel.

### Pagination

Keyset, never `OFFSET`: order by `(rank DESC NULLS LAST, page_id)` or `(meeting_date, page_id)` and pass the last row's key in the next-page link. Offset paging to page 4,000 of a 200k-item set would scan 200k rows.

### Snippets

`paradedb.snippet` requires a ParadeDB operator in the same query. For the visible page only, run `WHERE mp.id = ANY(%(visible_ids)s) AND mp.id @@@ paradedb.parse(%(highlight)s)`, where `highlight` is the set's original text, ORed with the sub-search when there is one. That costs one small query per page of results and highlights both the original hit and the refinement.

### Facets

The sidebar shows counts by disposition, code, state, municipality (top 20), year and document type for the current lens. They are `GROUP BY`s on the item table, cached in Redis under `(set_id, set.modified, lens hash)`. Any item change bumps `set.modified`, which invalidates them.

### Bulk actions

Every lens offers "mark all N relevant", "ignore all N" (with reason) and "apply code to all N". Each one is a single `UPDATE ... WHERE id IN (lens query)` or `INSERT INTO research_coding SELECT ... ON CONFLICT DO NOTHING`, behind a confirmation that shows N. It writes one audit event and stamps `last_event` on the affected items, so it can be undone.

### Save lens as set

Freezing a lens creates a child set with `origin=LENS`, `parent=<set>` and `query={"parent": id, "lens": lens.canonical()}`, filled by `INSERT ... SELECT` from the lens query. Dispositions are copied by default, because a child set usually continues the same triage; a checkbox resets them to unreviewed. Codings need no copying because they live on the project.

### Review mode

Triage of thousands of pages needs to be keyboard-driven. A focused single-page view walks the lens in order: `j`/`k` move, `r` marks relevant, `i` ignores (with an optional reason field), `1`–`9` toggle the first nine codes, `m` opens a memo. Alpine handles keys; each action is an htmx POST that returns the next page's partial.

## Check for new matches

"Check for new matches" re-runs the set's frozen query restricted to pages changed since the last check, and inserts only pages not already in the set. New items arrive unreviewed with `added_via=NEW_MATCHES`, so the lens "new matches, unreviewed" acts as an inbox.

The job is the materialization job with two differences:

- The spec gets `modified_after = set.last_checked_at or set.materialized_at`. Use `modified`, not `created`: a page whose text was re-OCR'd on backfill keeps its id, and may only now match. `ON CONFLICT DO NOTHING` drops pages already in the set, so a backfill that touches every row costs time but never duplicates.
- Ignore rules apply only to the newly inserted items.

It writes a `set.new_matches` audit event with the cutoff and the count added, then advances `last_checked_at`. Sets with `origin` MANUAL or LENS have no corpus query and do not offer the action.

### Sharing with alerting

The existing pipeline has a related limit worth fixing in the same change. `search_result_ids` fetches at most 10,000 ids, then filters on `created` in a second query, and logs a warning when it hits the cap. A broad saved search therefore under-reports new pages. With `modified_after` on `QuerySpec`, `search_new_pages` can push the cutoff into the BM25 query and drop the cap. Sets and saved searches then share one code path.

### Optional notification

A "Tell me when this set has new matches" toggle creates (via `Search.objects.get_or_create_for_params`) a `SavedSearch` for the set's spec. Its alert email links to the set with the check pre-armed. The set itself never changes until the user clicks, which keeps snapshot semantics and puts every membership change in the audit log.

## Ignore rules

An ignore rule is a `QuerySpec`-shaped predicate that marks matching **unreviewed** items ignored in every set in the project. A manual decision always beats a rule, and turning a rule off reverts only the items it ignored.

| Example rule | Predicate | Typical reason |
| --- | --- | --- |
| Procedural boilerplate | `text: "pledge of allegiance" OR "roll call"` | Boilerplate |
| A body out of scope | `meeting_name: ParksCommission` | Out of scope |
| Agendas when minutes exist | `document_type: agenda` | Duplicate of minutes |
| One city's notices | `municipality_ids: [...]`, `text: "public notice"` | Off-topic |

### Precedence

- A rule only touches items whose disposition is unreviewed.
- When a user changes a rule-ignored item by hand, `disposition_by_rule` is cleared and the item becomes a manual decision.
- Deactivating or deleting a rule sets items still ignored by it (`disposition_by_rule = rule`, `disposition = i`) back to unreviewed.

### Applying a rule

Creating or reactivating a rule enqueues one job for the whole project:

```sql
UPDATE research_setitem i
SET disposition = 'i', disposition_by_rule_id = %(rule)s, last_event_id = %(event)s
FROM meetings_meetingpage mp
WHERE i.page_id = mp.id
  AND i.result_set_id IN (SELECT id FROM research_resultset WHERE project_id = %(project)s)
  AND i.disposition = 'u'
  AND {build_where(rule.predicate, alias="mp")};
```

The same predicate runs during materialization and "check for new matches", restricted to the new items.

### Creating rules

- **From an item:** "Ignore pages like this" pre-fills the rule from the page's meeting body and document type.
- **From a lens:** "Turn this filter into an ignore rule" copies the lens's text and facet filters.
- **Preview before saving:** a dry run shows how many unreviewed items the rule would ignore, per set, with 10 sample pages. Rules are blunt, so the user should see what they catch before committing.

Item-level ignore reasons are free text, with a datalist of reasons already used in the project, so they converge on a small vocabulary without a managed list.

## Codebook, codings and memos

Codes are project-scoped labels with optional definition, inclusion and exclusion fields; v1 applies them to whole pages. The schema already carries span fields and leaves room for agenda items, so neither needs a data migration later.

### Codebook

- A tree (one level of nesting is enough for v1) with name, color, and pages-coded count per code.
- Definition, "apply when" and "do not apply when" are optional. The codebook shows a quiet marker on codes missing an exclusion rule, because that is the field that keeps coding consistent over months.
- Any coding can be marked an exemplar; exemplars show under the code's definition.
- **Merge** moves codings from one code to another (`INSERT ... ON CONFLICT DO NOTHING`, then delete) and writes a `code.merged` event. **Rename** and **archive** are logged too. Splitting stays manual: create the new code, recode, archive the old one.

### Codings

Applying a code creates a `Coding` on `(project, page)`, so the code follows the page into every set in the project. `quote` stores the page text at coding time (whole page in v1) and `page_hash` stores `MeetingPage.content_hash`. When the live hash differs, the page view shows "text changed since coded" and offers the old text.

### Memos

| Attached to | Used for |
| --- | --- |
| Project | Research journal: questions, hunches, what to do next |
| Set | Why this query, what it misses |
| Code | How the code's meaning has shifted |
| Page | Notes on one page (where old notebook notes land) |

Memos are Markdown, dated, and shown interleaved with audit events on the project timeline.

### Later: text spans

Selecting text in the page view creates a `Coding` with `span_start`/`span_end`. Offsets are Unicode code-point indexes into `MeetingPage.text`, which is Python `str` indexing. The browser's UTF-16 offsets must be converted on the way in. When the hash drifts, re-anchor by searching for `quote` in the new text (exact, then fuzzy); if neither matches, flag the coding for review. Code-point offsets on plain text also map directly to REFI-QDA text selections.

### Later: agenda items

Add a `Segment` model (`document`, start page and offset, end page and offset, item number, title, `source` parsed or manual) and a nullable `segment` FK on `Coding`, and later on `SetItem`. Segment-level sets are a new unit of analysis, so they would be a new set type alongside page sets, not a change to existing ones.

## Audit trail

Every decision that changes what a set contains or how a page is classified writes one append-only `AuditEvent`; reads and navigation do not. Its payoff for journalists is a generated "How we did this" summary; for academics it is the dependability audit trail Lincoln and Guba describe.

| Verb | Written when | Payload |
| --- | --- | --- |
| `set.materialized` | A set finishes materializing | Canonical spec, item count, per-rule ignore counts, duration |
| `set.new_matches` | Check for new matches runs | Cutoff, items added, per-rule ignore counts |
| `set.derived` | A lens is saved as a set | Parent id, lens spec, item count, dispositions copied or reset |
| `items.disposed` | One or many items change disposition | Lens spec or item ids (up to 100), from/to, reason, count |
| `items.coded` / `items.uncoded` | Codes applied or removed | Code id, lens spec or page ids (up to 100), count |
| `rule.created` / `rule.changed` / `rule.applied` / `rule.reverted` | Ignore rule lifecycle | Predicate before/after, counts per set |
| `code.created` / `code.changed` / `code.merged` / `code.archived` | Codebook changes | Fields before/after |
| `bulk.undone` | A bulk action is reverted | Original event id, count reverted |

Undo works by event: items whose `last_event` is the event being undone go back to their own `previous_disposition`. A bulk action over mixed dispositions therefore undoes exactly, one level deep.

### "How we did this" summary

A project page renders, from events alone, a short methods note the user can paste into a story or appendix:

- Each corpus query, verbatim, with the date it was run and its match count.
- The funnel per set: matched, ignored by each rule, ignored by hand, relevant.
- The codebook with definitions and counts.
- When new matches were last checked.

That is also the minimum an academic reviewer needs to follow a finding back to its queries and decisions.

## Export

v1 exports any lens as CSV, streamed and built in an RQ job above 10,000 rows. REFI-QDA (QDPX) comes later; nothing in the model blocks it.

### CSV (v1)

One row per item in the lens:

```csv
page_id,civic_band_url,municipality,state,meeting_name,meeting_date,document_type,page_number,disposition,ignore_reason,codes,memo,snippet
```

- `civic_band_url` is the citation journalists need: a link to the page on civic.band.
- `codes` is a `;`-joined list of code names; a second file, `codebook.csv`, holds definitions and counts.
- The "How we did this" summary is included as `methods.md`, so an export is self-describing.

### REFI-QDA (later)

The mapping is direct. Each `MeetingDocument` becomes a text source, with pages concatenated in order and page boundaries recorded as character offsets. A page-level coding becomes a text selection spanning that page; a span coding becomes a selection offset from the page start. Codes and their definitions map to the QDC codebook, memos to notes, and sets to REFI sets. Stored queries and dispositions have no REFI equivalent, so they travel only in the methods note.

## Notebooks migration and removal

Every notebook becomes a project with the same UUID, so old links redirect with a one-line view. Removal takes two releases: migrate and redirect first, drop the `notebooks` tables once the data is verified.

### Data mapping

| Notebooks | Research | Notes |
| --- | --- | --- |
| `Notebook` | `Project` (same id, name, archive flag) | Owner = `notebook.user` |
| All entries in a notebook | One `ResultSet` named "Saved pages", origin IMPORT | Items: disposition relevant, `added_via=IMPORT`, `added_at = entry.created` |
| `NotebookEntry.note` (non-empty) | `Memo` on the page | One memo per entry |
| `Tag` used in the notebook | `Code` in that project | Tags are user-scoped and codes project-scoped, so a tag used in three notebooks becomes three codes |
| `entry.tags` | `Coding` per (code, page) | Whole-page codings |
| `Tag` used nowhere | `Code` in the user's most recently modified project | So nothing silently disappears |

The migration is a `RunPython` in `research`, depending on the latest `notebooks` migration, and is idempotent (`get_or_create` keyed on the notebook id). It writes one `project.imported` audit event per project with counts.

### Callers to change

- `templates/meetings/partials/search_results.html` and `notebooks/partials/save_button.html` become an "Add to set" control. `meetings/views.py` computes `saved_page_ids` from items in the user's non-archived projects instead of `NotebookEntry`.
- `clip/views.py` (clipping from civic.band) adds to a set instead of a notebook.
- Navigation in `templates/base.html` and the feature blurb on `templates/homepage.html` point at `/research/`.
- `tests/factories.py` gains research factories; notebook factories and tests go in release 2.

The current fallback, "most recently modified notebook, or create My Notebook", carries over: "Add to set" defaults to the most recently used set, and creates a "My research" project with a "Saved pages" set if the user has none.

### Release plan

1. **Release 1:** ship `research`, run the data migration, add permanent redirects (`/notebooks/` → `/research/`, `/notebooks/<uuid>/` → `/research/<uuid>/`), remove Notebooks from navigation. Notebook views stay reachable only through the redirects.
2. **Verify:** a management command compares entry, note and tag counts per notebook against the migrated projects.
3. **Release 2:** delete the `notebooks` app and drop its tables.

## URLs, views and UI

The UI follows the existing stack: server-rendered templates, htmx partials for every mutation, Alpine for local state and keyboard handling. All views are `LoginRequiredMixin` and scope querysets to `project__owner=request.user`.

| URL | View | Purpose |
| --- | --- | --- |
| `/research/` | `ProjectListView` | Projects, with set counts and last activity |
| `/research/<project>/` | `ProjectDetailView` | Sets, codebook summary, rules, timeline of memos and events |
| `/research/<project>/sets/new/` | `SetCreateView` (POST) | From the search page's current query; enqueues materialization |
| `/research/<project>/sets/<set>/` | `SetDetailView` | Lens bar, facet sidebar, results, bulk-action bar |
| `.../sets/<set>/status/` | `SetStatusPartial` | Polled with `hx-trigger="every 2s"` until READY, FAILED or TOO\_LARGE |
| `.../sets/<set>/review/` | `ReviewView` | Keyboard triage of the current lens, one page at a time |
| `.../sets/<set>/items/<item>/disposition/` | `DispositionView` (POST) | Single item; returns the item partial |
| `.../sets/<set>/bulk/` | `BulkActionView` (POST) | Disposition or code over a lens; confirms with the count first |
| `.../sets/<set>/freeze/` | `FreezeLensView` (POST) | Save the current lens as a child set |
| `.../sets/<set>/new-matches/` | `NewMatchesView` (POST) | Enqueue check for new matches |
| `.../sets/<set>/export/` | `ExportView` | CSV of the current lens |
| `/research/<project>/codes/` | `CodebookView` | Tree, definitions, merge, archive |
| `/research/<project>/rules/` | `IgnoreRuleListView` | Rules, preview (dry run), activate, deactivate |
| `/research/<project>/methods/` | `MethodsView` | "How we did this" summary, copyable as Markdown |
| `/research/pages/<page>/` | `PageCodingPartial` | Codes and memos for a page within the active project |

### Entry points from search

The search results page gains "Save as set" next to the existing save-search control. It opens a small panel: pick or create a project, name the set, see the capped match count. Over the ceiling, the panel shows the count and suggests filters instead of a submit button.

### Set page layout

- **Lens bar** at the top: sub-search box, active filter chips, sort, "Save as set", "Make ignore rule".
- **Facet sidebar:** disposition, codes, state, municipality, year, document type, each with lens-scoped counts.
- **Results:** the current search-result card plus disposition buttons, code chips, a memo indicator, and the rule name when an item was ignored by a rule.
- **Bulk bar:** appears with the lens count, for "relevant", "ignore" and "apply code" over the whole lens.

## Performance risks and measurement plan

The design rests on several unmeasured assumptions about ParadeDB and Postgres; each needs an `EXPLAIN (ANALYZE, BUFFERS)` on a production-sized copy before the ceiling and query paths are final. The biggest long-term risk is not query speed but `research_setitem` growth.

| Risk | Why it matters | Measure | Fallback |
| --- | --- | --- | --- |
| BM25 index may not cover filter columns or `modified` | The 2026-10-02 perf audit found no in-repo index DDL; new-match checks filter on `modified` | `EXPLAIN` a filtered search and a `modified_after` search | Add `modified` to the BM25 index config, or a concurrent btree |
| Full enumeration plus insert for large sets | Enumerating 380k hits alone took about 1.9s; index maintenance on insert adds to it | Time `INSERT ... SELECT` at 50k, 200k and 500k items | Lower the ceiling; split the insert by state or year |
| `paradedb.score` inside `INSERT ... SELECT` without `ORDER BY` | Must be supported by the installed pg\_search version | One test query | Store rank NULL; default sort by date |
| Broad sub-search on a manual or lens set | Bare BM25 predicate scores the sub-term across 15.5M pages before the join | Time `text:(council)` against a 200k lens set | Add the set's date range and municipalities (from item min/max and distinct values) to the BM25 query |
| Facet `GROUP BY`s on large sets | Six aggregates per lens change | Time all six on a 500k set | Lazy-load the sidebar via htmx; cache longer |
| Ignore-rule `UPDATE` across all sets | Text predicate joined against every item in the project | Time on a project with three 200k sets | Run per set in separate jobs |
| `research_setitem` growth | 1,000 users × 5 sets × 200k items is 1B rows | Track rows per user after launch | Per-user item quota; hash-partition by `result_set_id` later |

### Benchmark command

Add `manage.py research_benchmark` that creates a throwaway project and runs the canonical cases: "housing" in California, "housing" nationwide (expect TOO\_LARGE), a 5-word phrase, a sub-search on each, a rule application and a new-matches check. It prints timings and buffer counts, so every PR touching these paths can quote before-and-after numbers, the way the existing `COUNT_CAP` comments do.

## Phasing and open questions

Phase 1 is the smallest release that replaces Notebooks; Phase 0 exists so its ceiling and query paths rest on measured numbers, not guesses.

&#91;embedded content: Delivery roadmap · 3 phases for v1, 2 gates\]

Phase 0 ships on its own and improves existing alerts. The two gates are hard stops: no set ceiling before benchmarks, no dropped tables before the notebook counts match.

### Open questions

- [ ] Placeholder: starting values for `RESEARCH_SET_MAX_ITEMS` and a per-user item quota are to be determined once the benchmark runs.
- [ ] Could you please confirm whether the out-of-band BM25 index includes `modified`, and the filter columns `build_where` uses?
- [ ] How often does backfill delete `MeetingPage` rows, rather than update them in place? That decides how much "page no longer in corpus" handling the UI needs.
- [ ] Is Research gated to a paid tier, like the rest of civic.observer's pro tools, or open to every signed-in user?
- [ ] Should a saved lens copy dispositions into the child set by default, or start everything unreviewed?
