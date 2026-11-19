# CivicBand Watch — Milestone 5: Place Extraction

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract every place from a thread deterministically, roll places up into subject and mentioned `ThreadPlace` rows, and add an optional LLM pass, labelling, and a precision/recall report.

**Architecture:** `watch/extraction.py` owns body-pattern rules, alias-based phrase matching, county/CDP rollup, and thread rollup. `watch/llm.py` is an optional OpenAI-compatible client behind settings and is disabled by default. `ThreadLabel` stores the operator’s gold labels; a report command compares predictions to labels per method.

**Tech Stack:** Django 5.2, regex, `difflib`, `httpx`, pytest, factory-boy.

**Spec:** `docs/superpowers/specs/2026-11-19-civicband-watch-design.md`

## Global Constraints

- Steps 1–3 and 5 are deterministic and must work with the LLM disabled.
- Every place resolves through `PlaceAlias` / `Place`. Never create a place from free text.
- Favor recall: low-confidence matches are stored and badged, not dropped.
- Manual `ThreadPlace` rows (`source="manual"`) are never overwritten by extraction.
- The LLM is off by default. The full test suite passes with `WATCH_LLM_ENABLED=False`.
- Do not send snippets to an LLM unless the feature is enabled. Enabling it is an operator decision that must be checked against Reddit’s Responsible Builder Policy and the provider’s data-retention terms.
- No migrations against `meetings` or `municipalities`.

## Review Focus

- A comment naming “Huntsville city council” adds Huntsville as a subject, while a passing “unlike New York” comparison stays mentioned.
- A “Beverly Hills Planning Commission” snippet in a general subreddit resolves Beverly Hills as the subject.
- A CDP with a known county rolls up to its county, because a CDP has no council.
- A common-word place name (`Mobile`, `Independence`, `Bell`) must not match every lowercase use.
- Re-running extraction must not duplicate `PlaceMention` or `ThreadPlace` rows, and must not overwrite manual corrections.

---

### Task 1: Body-pattern extraction and phrase resolution

**Files:**
- Create: `watch/extraction.py`
- Test: `tests/watch/test_body_patterns.py`

**Interfaces:**
- Produces:
  - `BODY_PATTERNS: list[tuple[re.Pattern[str], str, str]]` where the tuple is `(pattern, required_place_type, method)`.
  - `COMMON_WORD_NAMES: frozenset[str]` containing at least `mobile`, `independence`, `bell`, `hope`, `liberty`, `union`, `salem`, `athens`, `concord`, `madison`, `marion`, `clinton`, `jackson`, `washington`, `lebanon`, `riverside`.
  - `extract_body_patterns(text: str, *, state: str | None = None) -> list[ExtractedPlace]`
  - `resolve_place_phrase(phrase: str, *, state: str | None = None, required_place_type: str | None = None) -> Place | None`
  - `@dataclass(frozen=True) class ExtractedPlace: place: Place; matched_text: str; method: str; confidence: str; is_subject: bool`
- Patterns must cover:
  - `{Name} City Council` → city/town/village, subject.
  - `{Name} County Board of Supervisors` → county, subject.
  - `{Name} Board of Supervisors` → county, subject.
  - `{Name} Planning Commission` → any government place, subject.
  - `Town of {Name}`, `City of {Name}`, `County of {Name}` → matching type, subject.
- Resolution:
  - Normalize the phrase with `normalize_key`.
  - Query `PlaceAlias` exact matches; rank by state prior, then population.
  - If `required_place_type` is `county`, only county places are eligible.
  - If the phrase normalizes to a `COMMON_WORD_NAMES` entry and appears lower-case, require a capitalized match in the original text.
  - If multiple eligible places remain, choose the most populous only when at least 10× the next; otherwise return `None`.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.django_db
def test_city_council_pattern_resolves_subject(extraction_places):
    results = extract_body_patterns(
        "The Beverly Hills Planning Commission meets Tuesday."
    )
    assert [(r.place.name, r.is_subject) for r in results] == [("Beverly Hills", True)]


@pytest.mark.django_db
def test_county_board_resolves_county(extraction_places):
    results = extract_body_patterns("Riverside County Board of Supervisors voted yes.")
    assert results[0].place.place_type == "county"
    assert results[0].place.name == "Riverside"


@pytest.mark.django_db
def test_cdp_rolls_up_to_county(extraction_places):
    results = extract_body_patterns("The North Richmond Planning Commission met.")
    assert results[0].place.place_type == "county"


@pytest.mark.django_db
def test_common_word_place_requires_capitalization(extraction_places):
    assert extract_body_patterns("we need mobile city council reform") == []
    assert extract_body_patterns("Mobile City Council voted")[0].place.name == "Mobile"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/watch/test_body_patterns.py -q --no-cov`
Expected: FAIL because `watch.extraction` does not exist.

- [ ] **Step 3: Implement the patterns and resolver**

Use a bounded place phrase of 1–5 capitalized words:

```python
PLACE = r"(?P<place>[A-Z][A-Za-z'’-]+(?:\s+[A-Z][A-Za-z'’-]+){0,4})"
```

Match patterns case-insensitively but require the captured place to start uppercase. Query aliases in one query per pattern, not per word.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/watch/test_body_patterns.py -q --no-cov`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add watch/extraction.py tests/watch/test_body_patterns.py
git commit -m "feat(watch): extract places from body patterns"
```

---

### Task 2: Gazetteer phrase match for post and comment text

**Files:**
- Modify: `watch/extraction.py`
- Test: `tests/watch/test_phrase_match.py`

**Interfaces:**
- Produces:
  - `extract_phrases(text: str, *, state: str | None = None) -> list[ExtractedPlace]`
  - `extract_mention_places(mention: Mention, subreddit: Subreddit | None) -> list[PlaceMention]`
- Rules:
  - Generate 1–4 word n-grams from capitalized runs in `text`.
  - Query `PlaceAlias` with the normalized keys; use the subreddit’s state as a prior.
  - Exclude matches that are also common words unless the original phrase is capitalized.
  - Unanchored matches are `confidence="low"` and stored; body-pattern matches are `confidence="high"`.
  - Deduplicate by `(place, is_subject)` choosing the highest confidence.
  - `extract_mention_places` deletes existing non-manual `PlaceMention` rows for the mention, then bulk-creates the new ones. Manual rows survive.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.django_db
def test_unanchored_phrase_is_low_confidence(extraction_places):
    results = extract_phrases("A new grant for Chico was announced.", state="CA")
    assert results[0].place.name == "Chico"
    assert results[0].confidence == "low"


@pytest.mark.django_db
def test_state_prior_breaks_tie(extraction_places):
    results = extract_phrases("Springfield passed a budget.", state="IL")
    assert results[0].place.state == "IL"


@pytest.mark.django_db
def test_mention_extraction_is_idempotent(extraction_places):
    mention = MentionFactory(snippet_text="Chico needs housing.")
    subreddit = SubredditFactory(state="CA")
    first = extract_mention_places(mention, subreddit)
    second = extract_mention_places(mention, subreddit)
    assert [(p.place_id, p.method) for p in first] == [
        (p.place_id, p.method) for p in second
    ]
    assert PlaceMention.objects.filter(mention=mention).count() == len(first)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/watch/test_phrase_match.py -q --no-cov`
Expected: FAIL because `extract_phrases` does not exist.

- [ ] **Step 3: Implement phrase matching**

Use a single `PlaceAlias.objects.filter(key__in=keys).select_related("place")` per text. Sort candidates by state match, then population. Do not load the whole alias table into memory.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/watch/test_phrase_match.py -q --no-cov`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add watch/extraction.py tests/watch/test_phrase_match.py
git commit -m "feat(watch): match gazetteer phrases in post and comment text"
```

---

### Task 3: Thread rollup and enrichment wiring

**Files:**
- Modify: `watch/extraction.py`, `watch/enrichment.py`
- Test: `tests/watch/test_thread_rollup.py`

**Interfaces:**
- Produces: `extract_thread_places(thread: Thread) -> dict[str, int]` returning `{"subjects": ..., "mentioned": ..., "mentions": ...}`.
- Rollup rules:
  - Start with `thread.subreddit.place` as `role="subject"`, `source="subreddit"` when set.
  - For each mention, run `extract_mention_places`.
  - For post mentions, also run `extract_phrases` on `mention.post_title`; a place from a post title or post body is a subject.
  - A place from a comment is a subject only when its `PlaceMention.method == "body_pattern"`; otherwise mentioned.
  - Merge duplicates by place, preferring subject over mentioned and the highest confidence.
  - `update_or_create` on `(thread, place)`; never update a row with `source="manual"`.
  - Delete stale non-manual `ThreadPlace` rows that no longer appear.
- `enrich_thread` calls, in order:
  1. `ensure_subreddit_classification(thread)`: if `thread.subreddit` exists and is not `source="manual"` or has no `reviewed_at`, call `classify_subreddit(name)` and `apply_classification`; skip when the subreddit is already manual.
  2. `extract_thread_places(thread)`.
  3. `refresh_thread_hits(thread)`.
  4. Return `{"status": "ok", "thread_id": ..., "subjects": ..., "mentioned": ..., "hits": ...}`.
- Admin action `rerun_enrichment` enqueues `enrich_thread` for selected threads.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.django_db
def test_post_and_title_places_are_subjects(extraction_places):
    thread = ThreadFactory(title="Chico budget")
    MentionFactory(thread=thread, kind="post", snippet_text="Chico city council voted.")
    extract_thread_places(thread)
    assert ThreadPlace.objects.get(thread=thread, place__name="Chico").role == "subject"


@pytest.mark.django_db
def test_comment_body_pattern_promotes_to_subject(extraction_places):
    thread = ThreadFactory(title="McKinney recall")
    MentionFactory(
        thread=thread,
        kind="comment",
        snippet_text="Huntsville city council should do this.",
    )
    extract_thread_places(thread)
    assert (
        ThreadPlace.objects.get(thread=thread, place__name="Huntsville").role
        == "subject"
    )


@pytest.mark.django_db
def test_enrichment_classifies_unreviewed_subreddit(extraction_places, monkeypatch):
    subreddit = SubredditFactory(name="chicoca", source="gazetteer", reviewed_at=None)
    thread = ThreadFactory(subreddit=subreddit)
    monkeypatch.setattr(
        "watch.enrichment.classify_subreddit",
        lambda name: Classification(
            kind="place",
            place=PlaceFactory(name="Chico"),
            state="CA",
            country="US",
            confidence="high",
            source="gazetteer",
        ),
    )
    monkeypatch.setattr(
        "watch.enrichment.extract_thread_places",
        lambda t: {"subjects": 1, "mentioned": 0, "mentions": 1},
    )
    monkeypatch.setattr("watch.enrichment.refresh_thread_hits", lambda t: 0)
    result = enrich_thread(str(thread.id))
    assert result["status"] == "ok"
    subreddit.refresh_from_db()
    assert subreddit.place.name == "Chico"


@pytest.mark.django_db
def test_passing_comparison_stays_mentioned(extraction_places):
    thread = ThreadFactory(title="Los Angeles rent")
    MentionFactory(
        thread=thread, kind="comment", snippet_text="This is worse than New York."
    )
    extract_thread_places(thread)
    assert (
        ThreadPlace.objects.get(thread=thread, place__name="New York").role
        == "mentioned"
    )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/watch/test_thread_rollup.py -q --no-cov`
Expected: FAIL because `extract_thread_places` does not exist.

- [ ] **Step 3: Implement rollup and wiring**

Use a dict keyed by place id to merge roles. Wrap the delete/update/create sequence in `transaction.atomic`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/watch/test_thread_rollup.py -q --no-cov`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add watch/extraction.py watch/enrichment.py watch/admin.py tests/watch/test_thread_rollup.py
git commit -m "feat(watch): roll extracted places up to threads"
```

---

### Task 4: Optional LLM pass

**Files:**
- Create: `watch/llm.py`
- Modify: `config/settings/base.py`, `.env.example`, `watch/extraction.py`
- Test: `tests/watch/test_llm.py`

**Interfaces:**
- Produces settings:
  - `WATCH_LLM_ENABLED = env.bool("WATCH_LLM_ENABLED", False)`
  - `WATCH_LLM_BASE_URL = env.str("WATCH_LLM_BASE_URL", "")`
  - `WATCH_LLM_API_KEY = env.str("WATCH_LLM_API_KEY", "")`
  - `WATCH_LLM_MODEL = env.str("WATCH_LLM_MODEL", "")`
  - `WATCH_LLM_TIMEOUT = env.int("WATCH_LLM_TIMEOUT", 20)`
- Produces:
  - `@dataclass(frozen=True) class LLMPlace: name: str; state: str; role: str`
  - `@dataclass(frozen=True) class LLMResult: places: list[LLMPlace]; is_us: bool; topic: str`
  - `classify_with_llm(thread: Thread) -> LLMResult | None`
- `classify_with_llm` returns `None` immediately when disabled, when the base URL/model are blank, or on any exception. It never raises.
- Request shape: `POST {base_url}/chat/completions`, JSON body with `model`, `temperature=0`, `response_format={"type": "json_object"}`, and a system prompt requiring keys `places`, `is_us`, `topic`. Each `places` item has `name`, `state`, `role` (`subject`/`mentioned`).
- Trigger in `extract_thread_places` only when:
  - `settings.WATCH_LLM_ENABLED` is true, and
  - the subreddit kind is `general`, `region`, or `state`, or deterministic methods disagree with the subreddit, or a match is ambiguous.
- LLM results resolve names through `resolve_place_phrase`; unresolved names are ignored. LLM places are stored with `method="llm"`, `confidence="medium"`.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.django_db
def test_llm_disabled_makes_no_request(settings, monkeypatch):
    settings.WATCH_LLM_ENABLED = False
    monkeypatch.setattr("httpx.post", lambda *a, **k: pytest.fail("must not call"))
    assert classify_with_llm(ThreadFactory()) is None


@pytest.mark.django_db
def test_llm_parses_json(settings, monkeypatch):
    settings.WATCH_LLM_ENABLED = True
    settings.WATCH_LLM_BASE_URL = "https://llm.example/v1"
    settings.WATCH_LLM_MODEL = "small"
    monkeypatch.setattr(
        "httpx.post",
        lambda *a, **k: SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {
                "choices": [
                    {
                        "message": {
                            "content": '{"places": [{"name": "Chico", "state": "CA", "role": "subject"}], "is_us": true, "topic": "budget"}'
                        }
                    }
                ]
            },
        ),
    )
    result = classify_with_llm(ThreadFactory(title="Budget"))
    assert result.places[0].name == "Chico"
    assert result.is_us is True
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/watch/test_llm.py -q --no-cov`
Expected: FAIL because `watch.llm` does not exist.

- [ ] **Step 3: Implement the client and trigger**

Use `httpx.post(..., timeout=settings.WATCH_LLM_TIMEOUT)`. Validate every key with `isinstance` before constructing dataclasses. Catch `Exception` and log at `warning` without including snippet text.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/watch/test_llm.py -q --no-cov`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add watch/llm.py watch/extraction.py config/settings/base.py .env.example tests/watch/test_llm.py
git commit -m "feat(watch): add optional llm extraction pass"
```

---

### Task 5: `ThreadLabel` model, admin, and labelling workflow

**Files:**
- Modify: `watch/models.py`, `watch/migrations/0003_threadlabel.py`, `watch/admin.py`
- Test: `tests/watch/test_labels.py`

**Interfaces:**
- Produces `ThreadLabel`:
  - bigint pk
  - `thread` FK `Thread` CASCADE related `labels`
  - `place` FK `Place` null `SET_NULL` related `labels`
  - `is_subject` bool default false
  - `is_useful` bool null
  - `created_by` FK `users.User` null `SET_NULL`
  - `created_at` auto_now_add
  - unique `(thread, place)` where `place` is not null; allow one `place=None` row per thread via a second partial unique constraint.
- Admin:
  - `ThreadLabelInline` on `ThreadAdmin` with autocomplete for `place`.
  - `ThreadAdmin` action `mark_reviewed` sets `status="replied"`? No; add `labels` only. Add a `ThreadAdmin` filter `has_label`.
  - `ThreadLabelAdmin` list display thread, place, is_subject, is_useful, created_by.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.django_db
def test_thread_label_unique_per_place():
    thread = ThreadFactory()
    place = PlaceFactory()
    ThreadLabel.objects.create(thread=thread, place=place, is_subject=True)
    with pytest.raises(IntegrityError):
        ThreadLabel.objects.create(thread=thread, place=place, is_subject=False)


@pytest.mark.django_db
def test_thread_label_allows_one_no_place_row():
    thread = ThreadFactory()
    ThreadLabel.objects.create(thread=thread, place=None, is_useful=False)
    with pytest.raises(IntegrityError):
        ThreadLabel.objects.create(thread=thread, place=None, is_useful=False)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/watch/test_labels.py -q --no-cov`
Expected: FAIL because `ThreadLabel` does not exist.

- [ ] **Step 3: Implement the model, migration, and admin**

Use two `UniqueConstraint`s with `condition=Q(place__isnull=False)` and `condition=Q(place__isnull=True)`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/watch/test_labels.py -q --no-cov`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add watch/models.py watch/migrations watch/admin.py tests/watch/test_labels.py
git commit -m "feat(watch): record operator place labels"
```

---

### Task 6: Precision/recall report command

**Files:**
- Create: `watch/management/commands/watch_extraction_report.py`
- Test: `tests/watch/test_extraction_report.py`

**Interfaces:**
- Command `watch_extraction_report` prints, for each method/source, `precision`, `recall`, `tp`, `fp`, `fn`, and `labels`.
- Group predictions by `PlaceMention.method` and by `ThreadPlace.source`. A true positive is a predicted `(thread, place)` that appears in `ThreadLabel` with the same `is_subject`; false positive is predicted without a label; false negative is labelled without a prediction.
- Ignore labels where `place is None` for place-level precision/recall, but count them in a separate `threads_with_no_place` line.
- Options: `--since YYYY-MM-DD`, `--json`.

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.django_db
def test_report_prints_method_metrics(capsys):
    thread = ThreadFactory()
    place = PlaceFactory()
    MentionFactory(thread=thread)
    PlaceMention.objects.create(
        mention=thread.mentions.first(),
        place=place,
        matched_text="Chico",
        method="body_pattern",
        confidence="high",
        is_subject=True,
    )
    ThreadLabel.objects.create(thread=thread, place=place, is_subject=True)
    call_command("watch_extraction_report")
    out = capsys.readouterr().out
    assert "body_pattern" in out
    assert "precision" in out
    assert "1.00" in out
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/watch/test_extraction_report.py -q --no-cov`
Expected: FAIL because the command does not exist.

- [ ] **Step 3: Implement the report**

Use `defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0})`. Print a plain-text table by default and `json.dumps` with `--json`.

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/watch/test_extraction_report.py -q --no-cov`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add watch/management/commands/watch_extraction_report.py tests/watch/test_extraction_report.py
git commit -m "feat(watch): report extraction precision and recall"
```

---

### Task 7: Milestone verification

Run:

```bash
uv run pytest tests/watch -q --no-cov
uv run --group dev ruff check watch tests/watch
uv run --group dev mypy watch
```

Expected: all pass.

## Milestone 5 Acceptance

- With the LLM off, fixture cases resolve as specified:
  - “Beverly Hills Planning Commission” in a general subreddit gives Beverly Hills as subject.
  - A council-pattern comment naming another city adds that city as subject.
  - A passing comparison stays mentioned.
- A CDP with a known county rolls up to the county.
- Re-running extraction does not duplicate rows or overwrite manual corrections.
- Operator labels can be recorded in the admin.
- `watch_extraction_report` prints per-method precision and recall.
- `uv run pytest -q`, ruff, and mypy pass.
