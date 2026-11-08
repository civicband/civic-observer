# Research Projects — Phase 1D Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the project codebook (codes, codings, exemplars, merge/archive), page and project memos, the generated "How we did this" methods summary, and the CSV export.

**Architecture:** `research.codebook` owns code CRUD, coding apply/remove, exemplars and merge. `research.memos` owns memo CRUD and the project timeline. `research.methods` renders the methods note from audit events and current item state. `research.export` builds the CSV, codebook CSV and methods Markdown, streaming a zip in the request and handing large lenses to an RQ job. Views and HTMX partials live in `research/views.py`, `research/urls.py` and `templates/research/**`.

**Tech Stack:** Django 5.2, PostgreSQL 17, django-rq, htmx, Alpine, Tailwind, pytest, factory_boy. Markdown rendering uses `markdown-it-py` (already present transitively; add it as a direct dependency).

**Spec:** `docs/superpowers/specs/2026-11-06-research-projects-design.md`

## Global Constraints

- **Choice values are lowercase strings** (`"u"`, `"r"`, `"i"`, `"query"`, `"lens"`, `"manual"`, `"import"`, `"new_matches"`).
- **One event per decision.** Codebook changes write `code.created`, `code.changed`, `code.merged`, `code.archived`; coding changes write `items.coded` / `items.uncoded`. Memos are their own record and do not write an audit event. The methods summary and export write nothing.
- **Codes are project-scoped and one level deep.** A code's `parent` must belong to the same project and must itself be a root (`parent.parent_id is None`).
- **Whole-page codings only in v1.** `span_start`/`span_end` stay `None`; `quote` stores `MeetingPage.text`; `page_hash` stores `MeetingPage.content_hash`.
- **Codings are unique per `(code, page)`** for whole-page codings; duplicate applies are ignored, not errors.
- **Memos have at most one target** (project, set, code or page); more than one target in one request is a 400.
- **Owner scoping:** every view is `LoginRequiredMixin` and filters through `project__owner=request.user`; other users' projects/sets/codes/memos 404. Every mutation is POST + CSRF.
- **Page references:** `db_constraint=False, on_delete=DO_NOTHING`; a missing page must not 500. A missing page in a coding or set item renders "Page no longer in corpus" or is skipped in the codebook.
- **No silent truncation:** the export includes every row in the lens. Lenses above `RESEARCH_INLINE_EXPORT_MAX_ROWS` (default 10,000) are built by an RQ job and downloaded from the job result; they are never silently cut short.
- **Methods summary is generated, not authored.** It reads events and current item state; it never writes events.
- **Phase 1D excludes** notebook data migration, redirects and the `/research/` navigation/homepage/search/clip swaps. Do not stub them.

## Review Focus

1. The codebook tree is project-scoped and one-level; creating a child under a child is rejected; a duplicate name is rejected (Task 1).
2. Merging moves every coding from the source to the target without violating the unique constraints, deletes the source, and writes one `code.merged` event; merging a code with children is rejected (Task 1).
3. Applying/removing a code writes the right event and never touches another project; a duplicate apply writes no event; a missing page is skipped, not a 500 (Task 1).
4. The codebook views 404 for another user's project and code, and require POST + CSRF for create/rename/archive/merge (Task 2).
5. A memo's target must belong to the same project; more than one target is a 400; the timeline interleaves memos and events in date order (Task 4).
6. The methods summary is derived from events and current items only; it names each query, each set's funnel, the codebook with counts, and the last new-match check (Task 5).
7. The CSV export has the exact header order, includes every lens row, includes `codebook.csv` and `methods.md`, and uses the raw civic.band URL; above 10,000 rows it returns a job-backed download, not a truncated stream (Task 6).

---

### Task 1: Codebook service

**Files:**
- Create: `research/codebook.py`
- Test: `tests/research/test_codebook.py`

**Interfaces:**
- Consumes: `Code`, `Coding`, `Project`, `MeetingPage`, `Lens`, `filter_items`, `record_event`, `transaction`.
- Produces:
  - `codebook_tree(project, *, include_archived=False) -> list[dict]`
  - `create_code(project, *, name, parent=None, color="", definition="", inclusion="", exclusion="", actor=None) -> Code`
  - `rename_code(code, *, name=None, color=None, definition=None, inclusion=None, exclusion=None, actor=None) -> Code`
  - `archive_code(code, *, actor=None) -> Code`
  - `merge_codes(source, target, *, actor=None) -> int`
  - `apply_code_to_pages(project, code, page_ids, *, actor=None, result_set=None, lens=None) -> list[Coding]`
  - `apply_code_to_lens(project, code, result_set, lens, *, actor=None) -> list[Coding]`
  - `remove_code_from_pages(project, code, page_ids, *, actor=None, result_set=None, lens=None) -> int`
  - `remove_code_from_lens(project, code, result_set, lens, *, actor=None) -> int`
  - `set_exemplar(coding, *, is_exemplar=True, actor=None) -> Coding`

- [ ] **Step 1: Write the failing codebook tests**

Create `tests/research/test_codebook.py`:

```python
import pytest
from django.db import IntegrityError

from research.codebook import (
    apply_code_to_lens,
    apply_code_to_pages,
    archive_code,
    codebook_tree,
    create_code,
    merge_codes,
    remove_code_from_lens,
    remove_code_from_pages,
    rename_code,
    set_exemplar,
)
from research.lens import Lens
from research.models import Code, Coding
from tests.factories import (
    CodeFactory,
    CodingFactory,
    MeetingPageFactory,
    ProjectFactory,
    ResultSetFactory,
    SetItemFactory,
    UserFactory,
)


def _project_with_page():
    project = ProjectFactory()
    page = MeetingPageFactory(text="The council approved the housing plan.")
    result_set = ResultSetFactory(project=project)
    SetItemFactory(result_set=result_set, page=page)
    return project, page, result_set


@pytest.mark.django_db
class TestCodebookTree:
    def test_returns_roots_with_children_and_counts(self):
        project = ProjectFactory()
        parent = CodeFactory(project=project, name="Housing")
        child = CodeFactory(project=project, name="Affordable housing", parent=parent)
        page = MeetingPageFactory()
        CodingFactory(project=project, code=parent, page=page)
        CodingFactory(project=project, code=child, page=MeetingPageFactory())

        tree = codebook_tree(project)

        assert [node["code"].name for node in tree] == ["Housing"]
        assert tree[0]["coding_count"] == 1
        assert [node["code"].name for node in tree[0]["children"]] == [
            "Affordable housing"
        ]
        assert tree[0]["children"][0]["coding_count"] == 1

    def test_excludes_archived_by_default_and_can_include_them(self):
        project = ProjectFactory()
        CodeFactory(project=project, name="Live")
        CodeFactory(project=project, name="Retired", is_archived=True)

        assert [node["code"].name for node in codebook_tree(project)] == ["Live"]
        assert [
            node["code"].name for node in codebook_tree(project, include_archived=True)
        ] == [
            "Live",
            "Retired",
        ]

    def test_marks_codes_missing_an_exclusion_rule(self):
        project = ProjectFactory()
        CodeFactory(project=project, name="No exclusion")
        CodeFactory(project=project, name="Has exclusion", exclusion="Not about zoning")

        tree = codebook_tree(project)

        assert tree[0]["missing_exclusion"] is True
        assert tree[1]["missing_exclusion"] is False

    def test_lists_exemplars_under_their_code(self):
        project = ProjectFactory()
        code = CodeFactory(project=project)
        page = MeetingPageFactory(text="Exemplar text")
        CodingFactory(project=project, code=code, page=page, is_exemplar=True)

        tree = codebook_tree(project)

        assert [coding.page_id for coding in tree[0]["exemplars"]] == [page.id]


@pytest.mark.django_db
class TestCreateAndRenameCode:
    def test_creates_a_code_and_records_an_event(self):
        project = ProjectFactory()
        actor = UserFactory()

        code = create_code(
            project,
            name="Housing",
            color="#ff0000",
            definition="Housing policy",
            inclusion="When housing is discussed",
            exclusion="When housing is only mentioned in passing",
            actor=actor,
        )

        assert code.project == project
        assert code.name == "Housing"
        assert code.color == "#ff0000"
        event = project.events.get(verb="code.created")
        assert event.actor == actor
        assert event.payload["name"] == "Housing"

    def test_rejects_a_blank_name(self):
        project = ProjectFactory()

        with pytest.raises(ValueError):
            create_code(project, name="   ")

    def test_rejects_a_duplicate_sibling_name(self):
        project = ProjectFactory()
        create_code(project, name="Housing")

        with pytest.raises(ValueError):
            create_code(project, name="Housing")

    def test_rejects_a_child_of_a_child(self):
        project = ProjectFactory()
        parent = create_code(project, name="Housing")
        child = create_code(project, name="Affordable housing", parent=parent)

        with pytest.raises(ValueError):
            create_code(project, name="Too deep", parent=child)

    def test_rejects_a_parent_from_another_project(self):
        project = ProjectFactory()
        other = ProjectFactory()
        foreign = create_code(other, name="Foreign")

        with pytest.raises(ValueError):
            create_code(project, name="Child", parent=foreign)

    def test_rename_records_before_and_after(self):
        project = ProjectFactory()
        code = create_code(project, name="Housing")

        rename_code(code, name="Housing policy", color="#00ff00")

        code.refresh_from_db()
        assert code.name == "Housing policy"
        assert code.color == "#00ff00"
        event = project.events.get(verb="code.changed")
        assert event.payload["before"]["name"] == "Housing"
        assert event.payload["after"]["name"] == "Housing policy"


@pytest.mark.django_db
class TestArchiveCode:
    def test_archives_the_code_and_its_children(self):
        project = ProjectFactory()
        parent = create_code(project, name="Housing")
        child = create_code(project, name="Affordable housing", parent=parent)

        archive_code(parent)

        parent.refresh_from_db()
        child.refresh_from_db()
        assert parent.is_archived is True
        assert child.is_archived is True
        event = project.events.get(verb="code.archived")
        assert event.payload["children"] == [str(child.id)]


@pytest.mark.django_db
class TestMergeCodes:
    def test_moves_codings_and_deletes_the_source(self):
        project = ProjectFactory()
        source = create_code(project, name="Housing")
        target = create_code(project, name="Housing policy")
        page = MeetingPageFactory()
        CodingFactory(project=project, code=source, page=page)
        CodingFactory(project=project, code=target, page=MeetingPageFactory())

        moved = merge_codes(source, target)

        assert moved == 1
        assert Code.objects.filter(pk=source.pk).exists() is False
        assert Coding.objects.filter(code=target).count() == 2
        event = project.events.get(verb="code.merged")
        assert event.payload["source"] == str(source.id)
        assert event.payload["target"] == str(target.id)
        assert event.payload["moved"] == 1

    def test_does_not_duplicate_when_both_codes_have_the_page(self):
        project = ProjectFactory()
        source = create_code(project, name="Housing")
        target = create_code(project, name="Housing policy")
        page = MeetingPageFactory()
        CodingFactory(project=project, code=source, page=page)
        CodingFactory(project=project, code=target, page=page)

        moved = merge_codes(source, target)

        assert moved == 0
        assert Coding.objects.filter(code=target).count() == 1

    def test_rejects_merging_a_code_with_children(self):
        project = ProjectFactory()
        source = create_code(project, name="Housing")
        create_code(project, name="Affordable housing", parent=source)
        target = create_code(project, name="Housing policy")

        with pytest.raises(ValueError):
            merge_codes(source, target)

    def test_rejects_cross_project_merge(self):
        project = ProjectFactory()
        source = create_code(project, name="Housing")
        target = create_code(ProjectFactory(), name="Housing policy")

        with pytest.raises(ValueError):
            merge_codes(source, target)


@pytest.mark.django_db
class TestApplyAndRemoveCode:
    def test_apply_records_quote_hash_and_event(self):
        project, page, result_set = _project_with_page()
        code = create_code(project, name="Housing")
        actor = UserFactory()

        codings = apply_code_to_pages(
            project, code, [page.id], actor=actor, result_set=result_set
        )

        assert len(codings) == 1
        coding = Coding.objects.get(code=code, page=page)
        assert coding.quote == page.text
        assert coding.page_hash == page.content_hash
        assert coding.coder == actor
        event = result_set.project.events.get(verb="items.coded")
        assert event.payload["code"] == str(code.id)
        assert event.payload["page_ids"] == [page.id]
        assert event.payload["count"] == 1

    def test_duplicate_apply_writes_no_event(self):
        project, page, result_set = _project_with_page()
        code = create_code(project, name="Housing")
        apply_code_to_pages(project, code, [page.id], result_set=result_set)

        codings = apply_code_to_pages(project, code, [page.id], result_set=result_set)

        assert codings == []
        assert result_set.project.events.filter(verb="items.coded").count() == 1

    def test_skips_missing_pages(self):
        project, _page, result_set = _project_with_page()
        code = create_code(project, name="Housing")

        codings = apply_code_to_pages(
            project, code, ["missing-page"], result_set=result_set
        )

        assert codings == []

    def test_apply_to_lens_covers_only_the_lens(self):
        project = ProjectFactory()
        result_set = ResultSetFactory(project=project)
        in_lens = MeetingPageFactory(state="CA")
        outside = MeetingPageFactory(state="OR")
        SetItemFactory(result_set=result_set, page=in_lens, state="CA")
        SetItemFactory(result_set=result_set, page=outside, state="OR")
        code = create_code(project, name="Housing")

        codings = apply_code_to_lens(project, code, result_set, Lens(states=("CA",)))

        assert [coding.page_id for coding in codings] == [in_lens.id]

    def test_remove_records_uncoded_event(self):
        project, page, result_set = _project_with_page()
        code = create_code(project, name="Housing")
        apply_code_to_pages(project, code, [page.id], result_set=result_set)

        removed = remove_code_from_pages(
            project, code, [page.id], result_set=result_set
        )

        assert removed == 1
        assert Coding.objects.filter(code=code, page=page).exists() is False
        event = result_set.project.events.get(verb="items.uncoded")
        assert event.payload["code"] == str(code.id)
        assert event.payload["count"] == 1

    def test_remove_from_lens_covers_only_the_lens(self):
        project = ProjectFactory()
        result_set = ResultSetFactory(project=project)
        in_lens = MeetingPageFactory(state="CA")
        outside = MeetingPageFactory(state="OR")
        SetItemFactory(result_set=result_set, page=in_lens, state="CA")
        SetItemFactory(result_set=result_set, page=outside, state="OR")
        code = create_code(project, name="Housing")
        apply_code_to_pages(
            project, code, [in_lens.id, outside.id], result_set=result_set
        )

        removed = remove_code_from_lens(project, code, result_set, Lens(states=("CA",)))

        assert removed == 1
        assert Coding.objects.filter(code=code, page=in_lens).exists() is False
        assert Coding.objects.filter(code=code, page=outside).exists() is True


@pytest.mark.django_db
class TestExemplars:
    def test_marks_and_unmarks_an_exemplar(self):
        project, page, _result_set = _project_with_page()
        code = create_code(project, name="Housing")
        coding = Coding.objects.create(project=project, code=code, page=page)

        set_exemplar(coding, is_exemplar=True)
        coding.refresh_from_db()
        assert coding.is_exemplar is True

        set_exemplar(coding, is_exemplar=False)
        coding.refresh_from_db()
        assert coding.is_exemplar is False

    def test_exemplar_event_records_before_and_after(self):
        project, page, _result_set = _project_with_page()
        code = create_code(project, name="Housing")
        coding = Coding.objects.create(project=project, code=code, page=page)

        set_exemplar(coding, is_exemplar=True)

        event = project.events.get(verb="code.changed")
        assert event.payload["before"]["is_exemplar"] is False
        assert event.payload["after"]["is_exemplar"] is True
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `uv run --group test pytest tests/research/test_codebook.py -q --no-cov`

Expected: collection error (`ModuleNotFoundError: No module named 'research.codebook'`).

- [ ] **Step 3: Implement `research/codebook.py`**

Write the module with these behaviors:

- `codebook_tree(project, include_archived=False)`:
  - Query `Code.objects.filter(project=project)`; exclude `is_archived=True` unless `include_archived`.
  - Annotate `coding_count=Count("codings")`.
  - Build roots (`parent_id is None`) and attach children. Sort roots and children by `name`.
  - For each code, set `missing_exclusion = not bool(code.exclusion.strip())`.
  - For each code, set `exemplars = list(Coding.objects.filter(code=code, is_exemplar=True).select_related("page"))`.
  - Return a list of dicts: `{"code": code, "children": [...], "coding_count": code.coding_count, "missing_exclusion": bool, "exemplars": [...]}`. Children use the same shape.
- `create_code(...)`:
  - `name = name.strip()`; raise `ValueError("A code name is required")` if empty.
  - If `parent is not None`: reload `parent`; raise `ValueError` if `parent.project_id != project.id` or `parent.parent_id is not None`.
  - Try `Code.objects.create(...)`; catch `IntegrityError` and raise `ValueError("A code with that name already exists")`.
  - Write `record_event(project, "code.created", actor=actor, payload={"name": code.name, "parent": str(parent.id) if parent else None, "color": code.color, "definition": code.definition, "inclusion": code.inclusion, "exclusion": code.exclusion})`.
  - Return the code.
- `rename_code(code, ...)`:
  - Build `before` and `after` dicts for only the fields passed (not `None`).
  - If nothing changed, return `code` without writing an event.
  - Save with `update_fields=[... , "modified"]`.
  - Write `code.changed` with `{"before": before, "after": after}`.
- `archive_code(code)`:
  - Set `code.is_archived = True`; save `["is_archived", "modified"]`.
  - Collect descendants (`Code.objects.filter(parent=code)`); set each `is_archived=True` in one `update`.
  - Write one `code.archived` event with `{"name": code.name, "children": [str(child.id) for child in children]}`.
- `merge_codes(source, target)`:
  - Reload both; raise `ValueError` if projects differ, same id, or source has children (`Code.objects.filter(parent=source).exists()`).
  - Run one `INSERT ... SELECT` into `research_coding` copying rows from `source` to `target`:
    ```sql
    INSERT INTO research_coding
        (id, created, modified, project_id, code_id, page_id,
         span_start, span_end, quote, page_hash, is_exemplar, coder_id)
    SELECT gen_random_uuid(), now(), now(), project_id, %s, page_id,
           span_start, span_end, quote, page_hash, is_exemplar, coder_id
    FROM research_coding
    WHERE code_id = %s
    ON CONFLICT DO NOTHING
    ```
  - `moved = cur.rowcount`; then `Coding.objects.filter(code=source).delete()`; then `source.delete()`.
  - Write `code.merged` with `{"source": str(source.id), "target": str(target.id), "source_name": source.name, "target_name": target.name, "moved": moved}`.
  - Return `moved`.
- `apply_code_to_pages(project, code, page_ids, *, actor=None, result_set=None, lens=None)`:
  - Reload `code`; raise `ValueError` if `code.project_id != project.id`.
  - `page_ids = list(dict.fromkeys(page_ids))`; `pages = MeetingPage.objects.in_bulk(page_ids)`.
  - Build `Coding` rows for existing pages with `quote=page.text`, `page_hash=page.content_hash`, `coder=actor`.
  - `Coding.objects.bulk_create(rows, ignore_conflicts=True)`.
  - Determine created count with `Coding.objects.filter(code=code, page_id__in=[...]).count()` minus prior count, or count the rows not already present before the insert. Simplest: count existing first, then bulk create, then count again; `created = after - before`. If `created == 0`, return `[]` and write no event. If `created > 0`, write `items.coded` with `{"code": str(code.id), "count": created, "page_ids": [page.id for page in pages.values()][:100]}` plus `"lens": lens.canonical()` when `lens` is provided. Pass `result_set=result_set`.
  - Return the newly created codings. Because `bulk_create` returns objects without PKs when `ignore_conflicts=True`, re-query by `(code, page_id)` to return real rows.
- `apply_code_to_lens(project, code, result_set, lens, *, actor=None)`:
  - `page_ids = list(filter_items(result_set, lens).values_list("page_id", flat=True))`; call `apply_code_to_pages(..., result_set=result_set, lens=lens)`.
- `remove_code_from_pages(project, code, page_ids, *, actor=None, result_set=None, lens=None)`:
  - `deleted, _ = Coding.objects.filter(project=project, code=code, page_id__in=list(page_ids)).delete()`.
  - If `deleted == 0`, return 0 and write no event. Otherwise write `items.uncoded` with `{"code": str(code.id), "count": deleted, "page_ids": [...] [:100]}` plus `"lens"` when provided. Return `deleted`.
- `remove_code_from_lens(...)`: same via `filter_items`.
- `set_exemplar(coding, is_exemplar=True, actor=None)`:
  - Capture `before = coding.is_exemplar`; if equal, return `coding`.
  - Save `["is_exemplar", "modified"]`; write `code.changed` with `{"code": str(coding.code_id), "before": {"is_exemplar": before}, "after": {"is_exemplar": coding.is_exemplar}}`.
- Use `transaction.atomic()` around writes that touch more than one row.

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `uv run --group test pytest tests/research/test_codebook.py -q --no-cov`

Expected: all tests pass.

- [ ] **Step 5: Run the full suite and typecheck**

Run: `uv run --group test pytest tests/research -q --no-cov` and `uv run --group dev mypy research tests/research`.

Expected: pass/clean. Fix any failures before committing.

- [ ] **Step 6: Commit**

```bash
git add research/codebook.py tests/research/test_codebook.py
git commit -m "feat(research): add codebook service"
```

---

### Task 2: Codebook views and page coding partial

**Files:**
- Modify: `research/views.py`
- Modify: `research/urls.py`
- Create: `templates/research/codebook.html`
- Create: `templates/research/partials/codebook_tree.html`
- Create: `templates/research/partials/page_coding.html`
- Modify: `templates/research/project_detail.html` (Codebook link)
- Modify: `templates/research/partials/item.html` (Code link to the page partial)
- Test: `tests/research/test_codebook_views.py`

**Interfaces:**
- Consumes: `codebook_tree`, `create_code`, `rename_code`, `archive_code`, `merge_codes`, `apply_code_to_pages`, `remove_code_from_pages`, `Code`, `Coding`, `Project`, `MeetingPage`, `Memo`.
- Produces:
  - `CodebookView` (GET `/research/<project>/codes/`)
  - `CodeCreateView` (POST `/research/<project>/codes/new/`)
  - `CodeRenameView` (POST `/research/<project>/codes/<code>/rename/`)
  - `CodeArchiveView` (POST `/research/<project>/codes/<code>/archive/`)
  - `CodeMergeView` (POST `/research/<project>/codes/<code>/merge/`)
  - `PageCodingView` (GET `/research/<project>/pages/<page>/`)
  - `PageCodeView` (POST `/research/<project>/pages/<page>/codes/`)

- [ ] **Step 1: Write the failing codebook view tests**

Create `tests/research/test_codebook_views.py`:

```python
import pytest
from django.urls import reverse

from research.models import Code, Coding
from tests.factories import (
    CodeFactory,
    CodingFactory,
    MeetingPageFactory,
    ProjectFactory,
    ResultSetFactory,
    SetItemFactory,
    UserFactory,
)


def _login(client):
    user = UserFactory()
    client.force_login(user)
    return user


@pytest.mark.django_db
class TestCodebookView:
    def test_requires_login(self, client):
        project = ProjectFactory()
        response = client.get(reverse("research:codebook", args=[project.id]))
        assert response.status_code == 302

    def test_cannot_view_another_users_project(self, client):
        _login(client)
        project = ProjectFactory()
        response = client.get(reverse("research:codebook", args=[project.id]))
        assert response.status_code == 404

    def test_shows_tree_counts_and_missing_exclusion(self, client):
        user = _login(client)
        project = ProjectFactory(owner=user)
        parent = CodeFactory(project=project, name="Housing")
        CodeFactory(project=project, name="Housing policy", parent=parent)
        page = MeetingPageFactory()
        CodingFactory(project=project, code=parent, page=page)

        response = client.get(reverse("research:codebook", args=[project.id]))

        content = response.content.decode()
        assert response.status_code == 200
        assert "Housing" in content
        assert "Housing policy" in content
        assert "No exclusion rule" in content
        assert "1 coding" in content

    def test_shows_exemplar_text(self, client):
        user = _login(client)
        project = ProjectFactory(owner=user)
        code = CodeFactory(project=project, name="Housing")
        page = MeetingPageFactory(text="The council approved the housing plan.")
        CodingFactory(project=project, code=code, page=page, is_exemplar=True)

        response = client.get(reverse("research:codebook", args=[project.id]))

        assert "The council approved the housing plan." in response.content.decode()


@pytest.mark.django_db
class TestCodeMutations:
    def test_create_code_redirects_and_records_event(self, client):
        user = _login(client)
        project = ProjectFactory(owner=user)

        response = client.post(
            reverse("research:code-create", args=[project.id]),
            {"name": "Housing", "definition": "Housing policy"},
        )

        assert response.status_code == 302
        code = Code.objects.get(project=project, name="Housing")
        assert code.definition == "Housing policy"
        assert project.events.filter(verb="code.created").exists()

    def test_create_rejects_duplicate_with_400(self, client):
        user = _login(client)
        project = ProjectFactory(owner=user)
        CodeFactory(project=project, name="Housing")

        response = client.post(
            reverse("research:code-create", args=[project.id]), {"name": "Housing"}
        )

        assert response.status_code == 400
        assert "already exists" in response.content.decode()

    def test_create_requires_post(self, client):
        user = _login(client)
        project = ProjectFactory(owner=user)
        response = client.get(reverse("research:code-create", args=[project.id]))
        assert response.status_code == 405

    def test_rename_code_redirects(self, client):
        user = _login(client)
        project = ProjectFactory(owner=user)
        code = CodeFactory(project=project, name="Housing")

        response = client.post(
            reverse("research:code-rename", args=[project.id, code.id]),
            {"name": "Housing policy"},
        )

        assert response.status_code == 302
        code.refresh_from_db()
        assert code.name == "Housing policy"

    def test_cannot_rename_another_users_code(self, client):
        _login(client)
        project = ProjectFactory()
        code = CodeFactory(project=project, name="Housing")

        response = client.post(
            reverse("research:code-rename", args=[project.id, code.id]),
            {"name": "Hijacked"},
        )

        assert response.status_code == 404

    def test_archive_code_redirects(self, client):
        user = _login(client)
        project = ProjectFactory(owner=user)
        code = CodeFactory(project=project, name="Housing")

        response = client.post(
            reverse("research:code-archive", args=[project.id, code.id])
        )

        assert response.status_code == 302
        code.refresh_from_db()
        assert code.is_archived is True

    def test_merge_code_redirects(self, client):
        user = _login(client)
        project = ProjectFactory(owner=user)
        source = CodeFactory(project=project, name="Housing")
        target = CodeFactory(project=project, name="Housing policy")

        response = client.post(
            reverse("research:code-merge", args=[project.id, source.id]),
            {"target": str(target.id)},
        )

        assert response.status_code == 302
        assert Code.objects.filter(pk=source.pk).exists() is False

    def test_merge_rejects_cross_project_target(self, client):
        user = _login(client)
        project = ProjectFactory(owner=user)
        source = CodeFactory(project=project, name="Housing")
        target = CodeFactory(project=ProjectFactory(), name="Other")

        response = client.post(
            reverse("research:code-merge", args=[project.id, source.id]),
            {"target": str(target.id)},
        )

        assert response.status_code == 404


@pytest.mark.django_db
class TestPageCodingPartial:
    def test_shows_codes_and_memos_for_the_page(self, client):
        user = _login(client)
        project = ProjectFactory(owner=user)
        page = MeetingPageFactory(text="Housing plan")
        result_set = ResultSetFactory(project=project)
        SetItemFactory(result_set=result_set, page=page)
        code = CodeFactory(project=project, name="Housing")
        CodingFactory(project=project, code=code, page=page)
        project.memos.create(body="Worth revisiting", page=page)

        response = client.get(
            reverse("research:page-coding", args=[project.id, page.id])
        )

        content = response.content.decode()
        assert response.status_code == 200
        assert "Housing" in content
        assert "Worth revisiting" in content

    def test_apply_code_to_page(self, client):
        user = _login(client)
        project = ProjectFactory(owner=user)
        page = MeetingPageFactory()
        result_set = ResultSetFactory(project=project)
        SetItemFactory(result_set=result_set, page=page)
        code = CodeFactory(project=project, name="Housing")

        response = client.post(
            reverse("research:page-code", args=[project.id, page.id]),
            {"code": str(code.id), "action": "apply"},
        )

        assert response.status_code == 200
        assert Coding.objects.filter(project=project, code=code, page=page).exists()

    def test_remove_code_from_page(self, client):
        user = _login(client)
        project = ProjectFactory(owner=user)
        page = MeetingPageFactory()
        result_set = ResultSetFactory(project=project)
        SetItemFactory(result_set=result_set, page=page)
        code = CodeFactory(project=project, name="Housing")
        CodingFactory(project=project, code=code, page=page)

        response = client.post(
            reverse("research:page-code", args=[project.id, page.id]),
            {"code": str(code.id), "action": "remove"},
        )

        assert response.status_code == 200
        assert not Coding.objects.filter(project=project, code=code, page=page).exists()

    def test_cannot_view_another_users_page_coding(self, client):
        _login(client)
        project = ProjectFactory()
        page = MeetingPageFactory()

        response = client.get(
            reverse("research:page-coding", args=[project.id, page.id])
        )

        assert response.status_code == 404
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `uv run --group test pytest tests/research/test_codebook_views.py -q --no-cov`

Expected: `NoReverseMatch` for `research:codebook` and friends.

- [ ] **Step 3: Add the URLs**

Append to `research/urls.py`:

```python
path("<uuid:project>/codes/", views.CodebookView.as_view(), name="codebook"),
path("<uuid:project>/codes/new/", views.CodeCreateView.as_view(), name="code-create"),
path(
    "<uuid:project>/codes/<uuid:code>/rename/",
    views.CodeRenameView.as_view(),
    name="code-rename",
),
path(
    "<uuid:project>/codes/<uuid:code>/archive/",
    views.CodeArchiveView.as_view(),
    name="code-archive",
),
path(
    "<uuid:project>/codes/<uuid:code>/merge/",
    views.CodeMergeView.as_view(),
    name="code-merge",
),
path(
    "<uuid:project>/pages/<str:page>/",
    views.PageCodingView.as_view(),
    name="page-coding",
),
path(
    "<uuid:project>/pages/<str:page>/codes/",
    views.PageCodeView.as_view(),
    name="page-code",
),
```

- [ ] **Step 4: Implement the views**

Add to `research/views.py`:

```python
class CodebookView(LoginRequiredMixin, View):
    template_name = "research/codebook.html"

    def get(self, request, project):
        project_obj = get_object_or_404(Project, id=project, owner=request.user)
        return render(
            request,
            self.template_name,
            {
                "project": project_obj,
                "tree": codebook_tree(project_obj),
                "include_archived": False,
            },
        )
```

Also add `CodeCreateView`, `CodeRenameView`, `CodeArchiveView`, `CodeMergeView`, `PageCodingView`, `PageCodeView`:

- `CodeCreateView.post`: `project_obj = get_object_or_404(Project, id=project, owner=request.user)`; read `name`, `parent`, `color`, `definition`, `inclusion`, `exclusion`; resolve `parent = get_object_or_404(Code, id=parent, project=project_obj)` when present; call `create_code`; catch `ValueError` and re-render `codebook.html` with `error=str(exc)` and status 400; redirect to `research:codebook`.
- `CodeRenameView.post`: `code_obj = get_object_or_404(Code, id=code, project__owner=request.user, project_id=project)`; call `rename_code`; catch `ValueError` and render the codebook with status 400; redirect.
- `CodeArchiveView.post`: same lookup; call `archive_code`; redirect.
- `CodeMergeView.post`: same lookup for source; `target = get_object_or_404(Code, id=request.POST.get("target"), project=source.project)`; call `merge_codes`; catch `ValueError` and render the codebook with status 400; redirect.
- `PageCodingView.get`: `project_obj = get_object_or_404(Project, id=project, owner=request.user)`; `page = get_object_or_404(MeetingPage, id=page)`; ensure the page is in a set in the project: `if not SetItem.objects.filter(result_set__project=project_obj, page_id=page.id).exists(): raise Http404`; gather `codes = Coding.objects.filter(project=project_obj, page=page).select_related("code")`; `available_codes = Code.objects.filter(project=project_obj, is_archived=False).order_by("name")`; `memos = Memo.objects.filter(project=project_obj, page=page).order_by("-created")`; render `research/partials/page_coding.html`.
- `PageCodeView.post`: same lookup; `code = get_object_or_404(Code, id=request.POST.get("code"), project=project_obj)`; `action = request.POST.get("action")`; if `apply`, `apply_code_to_pages(project_obj, code, [page.id], actor=request.user)`; if `remove`, `remove_code_from_pages(...)`; else 400. Re-render the `page_coding.html` partial with the refreshed codes/memos. The partial's HTMX target is itself.
- Add `from django.http import Http404` and `from .codebook import ...` imports.

- [ ] **Step 5: Create the templates**

`templates/research/codebook.html` extends `base.html`; breadcrumb Research / project / Codebook; "New code" form (name, parent select of root codes, color, definition, inclusion, exclusion) posting to `research:code-create`; error block when `error`; includes `partials/codebook_tree.html`.

`templates/research/partials/codebook_tree.html` renders each root and child: name, color swatch, definition, "Apply when", "Do not apply when", "No exclusion rule" marker when missing, `N coding{{ count|pluralize }}`, exemplar text under the definition, rename form (inline, posts to `research:code-rename`), archive form (posts to `research:code-archive`), merge form (select of other non-archived codes, posts to `research:code-merge`). All forms include `{% csrf_token %}`.

`templates/research/partials/page_coding.html` is the HTMX partial: a list of applied codes with a "Remove" form for each; an "Apply code" select/form; and the page's memos rendered as plain text (`{{ memo.body }}`). Do not add a memo form or load the markdown filter here; Task 4 adds both. The partial's outer element has `id="page-coding-{{ page.id }}"`.

Modify `templates/research/project_detail.html` to add a "Codebook" link next to "Rules". Modify `templates/research/partials/item.html` to add a "Codes" link to `research:page-coding` with `hx-get`, `hx-target="#page-coding-{{ item.page_obj.id }}"`, `hx-swap="outerHTML"`, plus an empty `<div id="page-coding-...">` placeholder. Guard the link with `{% if item.page_obj %}`.

- [ ] **Step 6: Run the tests and confirm they pass**

Run: `uv run --group test pytest tests/research/test_codebook_views.py -q --no-cov`

Expected: pass. Then run `uv run --group test pytest tests/research -q --no-cov` and `uv run --group dev mypy research tests/research`; fix before committing.

- [ ] **Step 7: Commit**

```bash
git add research/views.py research/urls.py templates/research tests/research/test_codebook_views.py
git commit -m "feat(research): add codebook and page coding views"
```

---

### Task 3: Bulk code application

**Files:**
- Modify: `research/views.py` (`BulkActionView`, `SetDetailView` context)
- Modify: `templates/research/partials/bulk_bar.html`
- Modify: `templates/research/partials/bulk_confirm.html`
- Test: `tests/research/test_bulk_code.py`

**Interfaces:**
- Consumes: `apply_code_to_lens`, `Code`, `filter_items`, `Lens`, `ResultSet`.
- Produces: `BulkActionView` accepts a `code` field and applies that code to the whole lens; `SetDetailView` exposes `codes` in context.

- [ ] **Step 1: Write the failing bulk code tests**

Create `tests/research/test_bulk_code.py`:

```python
import pytest
from django.urls import reverse

from research.models import Coding
from tests.factories import (
    CodeFactory,
    MeetingPageFactory,
    ProjectFactory,
    ResultSetFactory,
    SetItemFactory,
    UserFactory,
)


def _setup():
    user = UserFactory()
    project = ProjectFactory(owner=user)
    result_set = ResultSetFactory(project=project, status="ready")
    ca = MeetingPageFactory(state="CA")
    or_ = MeetingPageFactory(state="OR")
    SetItemFactory(result_set=result_set, page=ca, state="CA")
    SetItemFactory(result_set=result_set, page=or_, state="OR")
    code = CodeFactory(project=project, name="Housing")
    return user, project, result_set, ca, or_, code


@pytest.mark.django_db
class TestBulkCodeApplication:
    def test_confirmation_shows_count_and_code(self, client):
        user, project, result_set, _ca, _or, code = _setup()
        client.force_login(user)

        response = client.post(
            reverse("research:set-bulk", args=[project.id, result_set.id]),
            {"code": str(code.id), "lens": "state=CA"},
        )

        content = response.content.decode()
        assert response.status_code == 200
        assert "Housing" in content
        assert "1 item" in content

    def test_confirm_applies_code_to_the_lens(self, client):
        user, project, result_set, ca, or_, code = _setup()
        client.force_login(user)

        response = client.post(
            reverse("research:set-bulk", args=[project.id, result_set.id]),
            {"code": str(code.id), "lens": "state=CA", "confirm": "1"},
        )

        assert response.status_code == 200
        assert Coding.objects.filter(code=code, page=ca).exists()
        assert not Coding.objects.filter(code=code, page=or_).exists()
        event = project.events.get(verb="items.coded")
        assert event.payload["count"] == 1

    def test_rejects_a_code_from_another_project(self, client):
        user, project, result_set, _ca, _or, _code = _setup()
        foreign = CodeFactory(project=ProjectFactory())
        client.force_login(user)

        response = client.post(
            reverse("research:set-bulk", args=[project.id, result_set.id]),
            {"code": str(foreign.id), "lens": "state=CA"},
        )

        assert response.status_code == 404

    def test_requires_ready_set(self, client):
        user, project, result_set, _ca, _or, code = _setup()
        result_set.status = "pending"
        result_set.save(update_fields=["status"])
        client.force_login(user)

        response = client.post(
            reverse("research:set-bulk", args=[project.id, result_set.id]),
            {"code": str(code.id), "lens": "state=CA"},
        )

        assert response.status_code == 409

    def test_requires_login(self, client):
        _user, project, result_set, _ca, _or, code = _setup()
        response = client.post(
            reverse("research:set-bulk", args=[project.id, result_set.id]),
            {"code": str(code.id)},
        )
        assert response.status_code == 302
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `uv run --group test pytest tests/research/test_bulk_code.py -q --no-cov`

Expected: the first test fails because the view validates `disposition` and returns 400 for a code-only request.

- [ ] **Step 3: Extend `BulkActionView`**

In `research/views.py`, import `apply_code_to_lens` and `Code`. Change the POST branch:

```python
code_id = request.POST.get("code")
if code_id:
    code = get_object_or_404(Code, id=code_id, project=result_set.project)
    if request.POST.get("confirm") != "1":
        count = filter_items(result_set, lens).count()
        return render(
            request,
            "research/partials/bulk_confirm.html",
            {
                "result_set": result_set,
                "lens": lens,
                "querystring": lens.to_querystring(),
                "code": code,
                "count": count,
            },
        )
    apply_code_to_lens(result_set.project, code, result_set, lens, actor=request.user)
    response = HttpResponse("")
    response["HX-Refresh"] = "true"
    return response
```

Keep the existing disposition branch unchanged below it. Add `context["codes"] = result_set.project.codes.filter(is_archived=False).order_by("name")` in `SetDetailView.get_context_data`.

- [ ] **Step 4: Update the bulk templates**

In `templates/research/partials/bulk_bar.html`, after the "Ignore all" form, add a code form:

```html
<form hx-post="{% url 'research:set-bulk' result_set.project_id result_set.id %}"
      hx-target="#bulk-confirm" hx-swap="innerHTML" class="inline-flex items-center gap-2">
    {% csrf_token %}
    <input type="hidden" name="lens" value="{{ querystring }}">
    <select name="code" class="rounded-md border-gray-300 text-xs shadow-sm">
        {% for code in codes %}<option value="{{ code.id }}">{{ code.name }}</option>{% endfor %}
    </select>
    <button type="submit" class="inline-flex items-center px-3 py-1.5 border border-gray-300 text-xs font-medium rounded-md text-gray-700 bg-white hover:bg-gray-50">Apply code</button>
</form>
```

In `templates/research/partials/bulk_confirm.html`, branch on `code`:

```html
{% if code %}
    <p>Apply code "{{ code.name }}" to all {{ count }} item{{ count|pluralize }} in this view?</p>
    <input type="hidden" name="code" value="{{ code.id }}">
{% else %}
    <p>Change all {{ count }} item{{ count|pluralize }} in this view to "{{ disposition_label }}"?</p>
    <input type="hidden" name="disposition" value="{{ disposition }}">
    <input type="hidden" name="reason" value="{{ reason }}">
{% endif %}
```

Keep the `lens`, `confirm=1`, CSRF, Confirm and Cancel controls.

- [ ] **Step 5: Run the tests and confirm they pass**

Run: `uv run --group test pytest tests/research/test_bulk_code.py -q --no-cov`, then the full `tests/research` suite and `mypy research tests/research`.

- [ ] **Step 6: Commit**

```bash
git add research/views.py templates/research/partials/bulk_bar.html templates/research/partials/bulk_confirm.html tests/research/test_bulk_code.py
git commit -m "feat(research): apply codes to a whole lens"
```

---

### Task 4: Memos and project timeline

**Files:**
- Create: `research/memos.py`
- Create: `research/templatetags/__init__.py`
- Create: `research/templatetags/research_markdown.py`
- Modify: `research/views.py` (`ProjectDetailView` timeline, memo views)
- Modify: `research/urls.py`
- Modify: `templates/research/project_detail.html`
- Modify: `templates/research/set_detail.html` (memo form)
- Modify: `templates/research/codebook.html` (memo form)
- Modify: `templates/research/partials/page_coding.html` (memo form)
- Modify: `pyproject.toml` (add `markdown-it-py`)
- Test: `tests/research/test_memos.py`

**Interfaces:**
- Consumes: `Memo`, `Project`, `ResultSet`, `Code`, `MeetingPage`, `AuditEvent`, `record_event` (not for memos).
- Produces:
  - `create_memo(project, *, author, body, result_set=None, code=None, page=None) -> Memo`
  - `update_memo(memo, *, body) -> Memo`
  - `delete_memo(memo) -> None`
  - `project_timeline(project) -> list[dict]`
  - `MemoCreateView` (POST `/research/<project>/memos/`)
  - `MemoUpdateView` (POST `/research/<project>/memos/<memo>/`)
  - `MemoDeleteView` (POST `/research/<project>/memos/<memo>/delete/`)

- [ ] **Step 1: Write the failing memo tests**

Create `tests/research/test_memos.py`:

```python
import pytest
from django.urls import reverse

from research.memos import create_memo, delete_memo, project_timeline, update_memo
from research.models import Memo
from tests.factories import (
    AuditEventFactory,
    CodeFactory,
    MeetingPageFactory,
    MemoFactory,
    ProjectFactory,
    ResultSetFactory,
    SetItemFactory,
    UserFactory,
)


def _login(client):
    user = UserFactory()
    client.force_login(user)
    return user


@pytest.mark.django_db
class TestMemoService:
    def test_creates_a_project_memo(self):
        project = ProjectFactory()
        author = UserFactory()

        memo = create_memo(project, author=author, body="  First hunch  ")

        assert memo.project == project
        assert memo.author == author
        assert memo.body == "First hunch"
        assert memo.result_set_id is None
        assert memo.code_id is None
        assert memo.page_id is None

    def test_rejects_more_than_one_target(self):
        project = ProjectFactory()
        result_set = ResultSetFactory(project=project)
        code = CodeFactory(project=project)

        with pytest.raises(ValueError):
            create_memo(
                project,
                author=UserFactory(),
                body="Too many targets",
                result_set=result_set,
                code=code,
            )

    def test_rejects_a_set_from_another_project(self):
        project = ProjectFactory()
        foreign = ResultSetFactory(project=ProjectFactory())

        with pytest.raises(ValueError):
            create_memo(
                project, author=UserFactory(), body="Wrong project", result_set=foreign
            )

    def test_rejects_a_blank_body(self):
        project = ProjectFactory()

        with pytest.raises(ValueError):
            create_memo(project, author=UserFactory(), body="   ")

    def test_update_and_delete(self):
        project = ProjectFactory()
        memo = create_memo(project, author=UserFactory(), body="Old")

        update_memo(memo, body="New")
        memo.refresh_from_db()
        assert memo.body == "New"

        delete_memo(memo)
        assert Memo.objects.filter(pk=memo.pk).exists() is False

    def test_timeline_interleaves_memos_and_events_by_date(self):
        project = ProjectFactory()
        older = create_memo(project, author=UserFactory(), body="Older memo")
        event = AuditEventFactory(project=project)
        newer = create_memo(project, author=UserFactory(), body="Newer memo")

        timeline = project_timeline(project)

        assert [entry["kind"] for entry in timeline] == ["memo", "event", "memo"]
        assert timeline[0]["memo"] == newer
        assert timeline[1]["event"] == event
        assert timeline[2]["memo"] == older


@pytest.mark.django_db
class TestMemoViews:
    def test_create_project_memo_redirects(self, client):
        user = _login(client)
        project = ProjectFactory(owner=user)

        response = client.post(
            reverse("research:memo-create", args=[project.id]),
            {"body": "A project note"},
        )

        assert response.status_code == 302
        assert project.memos.filter(body="A project note", author=user).exists()

    def test_create_set_memo_redirects_to_set(self, client):
        user = _login(client)
        project = ProjectFactory(owner=user)
        result_set = ResultSetFactory(project=project)

        response = client.post(
            reverse("research:memo-create", args=[project.id]),
            {"body": "Why this query", "result_set": str(result_set.id)},
        )

        assert response.status_code == 302
        assert response.url == reverse(
            "research:set-detail", args=[project.id, result_set.id]
        )
        assert result_set.memos.filter(body="Why this query").exists()

    def test_create_code_memo_redirects_to_codebook(self, client):
        user = _login(client)
        project = ProjectFactory(owner=user)
        code = CodeFactory(project=project)

        response = client.post(
            reverse("research:memo-create", args=[project.id]),
            {"body": "Meaning shifted", "code": str(code.id)},
        )

        assert response.status_code == 302
        assert response.url == reverse("research:codebook", args=[project.id])
        assert code.memos.filter(body="Meaning shifted").exists()

    def test_create_page_memo_requires_page_in_project(self, client):
        user = _login(client)
        project = ProjectFactory(owner=user)
        page = MeetingPageFactory()

        response = client.post(
            reverse("research:memo-create", args=[project.id]),
            {"body": "Orphan page", "page": page.id},
        )

        assert response.status_code == 404

    def test_create_page_memo_redirects(self, client):
        user = _login(client)
        project = ProjectFactory(owner=user)
        result_set = ResultSetFactory(project=project)
        page = MeetingPageFactory()
        SetItemFactory(result_set=result_set, page=page)

        response = client.post(
            reverse("research:memo-create", args=[project.id]),
            {
                "body": "Note on the page",
                "page": page.id,
                "next": reverse(
                    "research:set-detail", args=[project.id, result_set.id]
                ),
            },
        )

        assert response.status_code == 302
        assert project.memos.filter(body="Note on the page", page=page).exists()

    def test_create_rejects_two_targets(self, client):
        user = _login(client)
        project = ProjectFactory(owner=user)
        result_set = ResultSetFactory(project=project)
        code = CodeFactory(project=project)

        response = client.post(
            reverse("research:memo-create", args=[project.id]),
            {
                "body": "Two targets",
                "result_set": str(result_set.id),
                "code": str(code.id),
            },
        )

        assert response.status_code == 400

    def test_cannot_create_on_another_users_project(self, client):
        _login(client)
        project = ProjectFactory()

        response = client.post(
            reverse("research:memo-create", args=[project.id]), {"body": "Nope"}
        )

        assert response.status_code == 404

    def test_update_and_delete_are_owner_scoped(self, client):
        user = _login(client)
        project = ProjectFactory(owner=user)
        memo = MemoFactory(project=project, body="Old")

        response = client.post(
            reverse("research:memo-update", args=[project.id, memo.id]),
            {"body": "New"},
        )
        assert response.status_code == 302
        memo.refresh_from_db()
        assert memo.body == "New"

        response = client.post(
            reverse("research:memo-delete", args=[project.id, memo.id])
        )
        assert response.status_code == 302
        assert Memo.objects.filter(pk=memo.pk).exists() is False

    def test_cannot_update_another_users_memo(self, client):
        _login(client)
        project = ProjectFactory()
        memo = MemoFactory(project=project, body="Old")

        response = client.post(
            reverse("research:memo-update", args=[project.id, memo.id]),
            {"body": "Hijacked"},
        )
        assert response.status_code == 404

    def test_project_detail_shows_timeline(self, client):
        user = _login(client)
        project = ProjectFactory(owner=user)
        project.memos.create(body="A project note", author=user)
        AuditEventFactory(project=project, verb="set.materialized")

        response = client.get(reverse("research:project-detail", args=[project.id]))

        content = response.content.decode()
        assert "A project note" in content
        assert "set.materialized" in content
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `uv run --group test pytest tests/research/test_memos.py -q --no-cov`

Expected: `ModuleNotFoundError: No module named 'research.memos'`.

- [ ] **Step 3: Implement `research/memos.py`**

```python
"""Memos and the project timeline."""

from __future__ import annotations

from itertools import chain

from .models import Code, Memo, Project, ResultSet


def _clean_body(body: str) -> str:
    body = (body or "").strip()
    if not body:
        raise ValueError("A memo needs a body")
    return body


def _validate_targets(project, result_set, code, page) -> None:
    targets = [result_set, code, page]
    if sum(target is not None for target in targets) > 1:
        raise ValueError("A memo can target only one thing")
    if result_set is not None and result_set.project_id != project.id:
        raise ValueError("The set belongs to another project")
    if code is not None and code.project_id != project.id:
        raise ValueError("The code belongs to another project")


def create_memo(
    project, *, author, body, result_set=None, code=None, page=None
) -> Memo:
    _validate_targets(project, result_set, code, page)
    return Memo.objects.create(
        project=project,
        author=author,
        body=_clean_body(body),
        result_set=result_set,
        code=code,
        page=page,
    )


def update_memo(memo: Memo, *, body: str) -> Memo:
    memo.body = _clean_body(body)
    memo.save(update_fields=["body", "modified"])
    return memo


def delete_memo(memo: Memo) -> None:
    memo.delete()


def project_timeline(project) -> list[dict]:
    entries = [
        {"kind": "memo", "created": memo.created, "memo": memo}
        for memo in project.memos.select_related("author", "result_set", "code", "page")
    ]
    entries.extend(
        {"kind": "event", "created": event.created, "event": event}
        for event in project.events.select_related("actor", "result_set")
    )
    entries.sort(key=lambda entry: entry["created"], reverse=True)
    return entries
```

- [ ] **Step 4: Add the markdown filter**

Create `research/templatetags/__init__.py` (empty) and `research/templatetags/research_markdown.py`:

```python
"""Render memo Markdown safely."""

from django import template
from django.utils.safestring import mark_safe
from markdown_it import MarkdownIt

register = template.Library()

_renderer = MarkdownIt("commonmark", {"html": False})


@register.filter
def research_markdown(value: str) -> str:
    return mark_safe(_renderer.render(value or ""))
```

Add `"markdown-it-py>=3.0.0"` to `[project] dependencies` in `pyproject.toml` and run `uv lock`.

- [ ] **Step 5: Add the memo URLs and views**

Append to `research/urls.py`:

```python
path("<uuid:project>/memos/", views.MemoCreateView.as_view(), name="memo-create"),
path(
    "<uuid:project>/memos/<uuid:memo>/",
    views.MemoUpdateView.as_view(),
    name="memo-update",
),
path(
    "<uuid:project>/memos/<uuid:memo>/delete/",
    views.MemoDeleteView.as_view(),
    name="memo-delete",
),
```

Add to `research/views.py`:

```python
class MemoCreateView(LoginRequiredMixin, View):
    def post(self, request, project):
        project_obj = get_object_or_404(Project, id=project, owner=request.user)
        result_set = _memo_target(
            ResultSet, request.POST.get("result_set"), project_obj
        )
        code = _memo_target(Code, request.POST.get("code"), project_obj)
        page_id = request.POST.get("page")
        page = None
        if page_id:
            if not SetItem.objects.filter(
                result_set__project=project_obj, page_id=page_id
            ).exists():
                raise Http404
            page = MeetingPage.objects.get(pk=page_id)
        try:
            create_memo(
                project_obj,
                author=request.user,
                body=request.POST.get("body", ""),
                result_set=result_set,
                code=code,
                page=page,
            )
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))
        if result_set is not None:
            return redirect(
                "research:set-detail", project=project_obj.id, set=result_set.id
            )
        if code is not None:
            return redirect("research:codebook", project=project_obj.id)
        next_url = request.POST.get("next")
        if next_url and url_has_allowed_host_and_scheme(
            next_url, allowed_hosts={request.get_host()}
        ):
            return redirect(next_url)
        return redirect("research:project-detail", project=project_obj.id)
```

Add helper `_memo_target(model, raw_id, project)` returning `None` or `get_object_or_404(model, id=raw_id, project=project)`. Add `MemoUpdateView` and `MemoDeleteView` that look up `Memo` via `get_object_or_404(Memo, id=memo, project__owner=request.user, project_id=project)` and redirect to the project detail. Add `timeline` to `ProjectDetailView.get_context_data`.

- [ ] **Step 6: Update the templates**

`project_detail.html`: add a "Codebook" link and a "New memo" form; render `timeline` with memo bodies via `|research_markdown` and event verbs/dates. `set_detail.html`: add a "Memo" form posting to `research:memo-create` with `result_set` hidden and `next` set to the set URL. `codebook.html`: add a "Memo" form with `code` hidden. `page_coding.html`: add a "Memo" form with `page` hidden and `next` set to the set URL. Load `research_markdown` with `{% load research_markdown %}` in each template that renders a memo.

- [ ] **Step 7: Run the tests and confirm they pass**

Run: `uv run --group test pytest tests/research/test_memos.py -q --no-cov`, then the full `tests/research` suite and `mypy research tests/research`.

- [ ] **Step 8: Commit**

```bash
git add research/memos.py research/templatetags research/views.py research/urls.py templates/research pyproject.toml uv.lock tests/research/test_memos.py
git commit -m "feat(research): add memos and project timeline"
```

---

### Task 5: "How we did this" methods summary

**Files:**
- Create: `research/methods.py`
- Modify: `research/views.py` (`MethodsView`)
- Modify: `research/urls.py`
- Create: `templates/research/methods.html`
- Modify: `templates/research/project_detail.html` (Methods link)
- Test: `tests/research/test_methods.py`

**Interfaces:**
- Consumes: `Project`, `ResultSet`, `SetItem`, `Code`, `AuditEvent`, `QuerySpec`, `Lens`, `build_methods`.
- Produces:
  - `build_methods(project) -> str`
  - `MethodsView` (GET `/research/<project>/methods/`)

- [ ] **Step 1: Write the failing methods tests**

Create `tests/research/test_methods.py`:

```python
from datetime import datetime, timezone

import pytest
from django.urls import reverse

from research.methods import build_methods
from tests.factories import (
    AuditEventFactory,
    CodeFactory,
    CodingFactory,
    MeetingPageFactory,
    ProjectFactory,
    ResultSetFactory,
    SetItemFactory,
    UserFactory,
)


@pytest.mark.django_db
class TestBuildMethods:
    def test_includes_each_query_with_date_and_count(self):
        project = ProjectFactory(name="Housing project")
        AuditEventFactory(
            project=project,
            verb="set.materialized",
            payload={
                "query": {"text": "housing", "states": ["CA"]},
                "item_count": 42,
            },
        )

        markdown = build_methods(project)

        assert "# How we did this" in markdown
        assert "housing" in markdown
        assert "CA" in markdown
        assert "42" in markdown

    def test_includes_the_funnel_per_set(self):
        project = ProjectFactory()
        result_set = ResultSetFactory(project=project, name="Snapshot", item_count=10)
        SetItemFactory(result_set=result_set, disposition="r")
        SetItemFactory(result_set=result_set, disposition="i")
        SetItemFactory(result_set=result_set, disposition="u")

        markdown = build_methods(project)

        assert "Snapshot" in markdown
        assert "Relevant: 1" in markdown
        assert "Ignored by hand: 1" in markdown
        assert "Unreviewed: 1" in markdown

    def test_includes_rule_ignores_by_rule_name(self):
        project = ProjectFactory()
        result_set = ResultSetFactory(project=project, name="Snapshot", item_count=2)
        rule = project.ignore_rules.create(name="Boilerplate", predicate={"text": "x"})
        SetItemFactory(result_set=result_set, disposition="i", disposition_by_rule=rule)

        markdown = build_methods(project)

        assert "Ignored by rule Boilerplate: 1" in markdown

    def test_includes_the_codebook_with_counts_and_definitions(self):
        project = ProjectFactory()
        code = CodeFactory(
            project=project,
            name="Housing",
            definition="Housing policy",
            exclusion="Only mentioned in passing",
        )
        CodingFactory(project=project, code=code, page=MeetingPageFactory())

        markdown = build_methods(project)

        assert "Housing" in markdown
        assert "Housing policy" in markdown
        assert "Only mentioned in passing" in markdown
        assert "Codings: 1" in markdown

    def test_includes_last_new_match_check(self):
        project = ProjectFactory()
        result_set = ResultSetFactory(project=project, name="Snapshot")
        result_set.last_checked_at = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)
        result_set.save(update_fields=["last_checked_at"])

        markdown = build_methods(project)

        assert "Last checked for new matches" in markdown


@pytest.mark.django_db
class TestMethodsView:
    def test_requires_login(self, client):
        project = ProjectFactory()
        response = client.get(reverse("research:methods", args=[project.id]))
        assert response.status_code == 302

    def test_cannot_view_another_users_project(self, client):
        user = UserFactory()
        client.force_login(user)
        project = ProjectFactory()
        response = client.get(reverse("research:methods", args=[project.id]))
        assert response.status_code == 404

    def test_renders_the_methods_note(self, client):
        user = UserFactory()
        client.force_login(user)
        project = ProjectFactory(owner=user, name="Housing project")
        AuditEventFactory(
            project=project,
            verb="set.materialized",
            payload={"query": {"text": "housing"}, "item_count": 7},
        )

        response = client.get(reverse("research:methods", args=[project.id]))

        content = response.content.decode()
        assert response.status_code == 200
        assert "How we did this" in content
        assert "housing" in content
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `uv run --group test pytest tests/research/test_methods.py -q --no-cov`

Expected: `ModuleNotFoundError: No module named 'research.methods'`.

- [ ] **Step 3: Implement `research/methods.py`**

Write `build_methods(project)` returning Markdown:

- Header: `# How we did this — {project.name}` and a blank line.
- `## Queries`:
  - For each `project.events.filter(verb="set.materialized").order_by("created")`: `- {describe_spec(QuerySpec.from_canonical(event.payload.get("query", {})))} ({event.created:%Y-%m-%d}) — {event.payload.get("item_count", 0)} matches`.
  - For each `set.new_matches`: `- Checked for new matches on {event.created:%Y-%m-%d} (cutoff {event.payload.get("cutoff", "")}): {event.payload.get("added", 0)} added`.
  - For each `set.derived`: `- Frozen lens {describe_lens(event.payload.get("lens", {}))} ({event.created:%Y-%m-%d}) — {event.payload.get("item_count", 0)} items`.
- `## Funnel`:
  - For each `project.sets.order_by("created")`: `### {set.name}`; compute counts:
    - `matched = result_set.item_count`
    - `ignored_by_rule` grouped by `disposition_by_rule__name` for items with `disposition="i"` and `disposition_by_rule__isnull=False`.
    - `ignored_by_hand = items.filter(disposition="i", disposition_by_rule__isnull=True).count()`
    - `relevant = items.filter(disposition="r").count()`
    - `unreviewed = items.filter(disposition="u").count()`
  - Render `- Matched: {matched}`, `- Ignored by rule {name}: {count}` for each, `- Ignored by hand: {count}`, `- Relevant: {count}`, `- Unreviewed: {count}`.
- `## Codebook`:
  - For each `project.codes.filter(is_archived=False).order_by("name")`: `### {code.name}`; `- Definition: {code.definition or "—"}`; `- Apply when: {code.inclusion or "—"}`; `- Do not apply when: {code.exclusion or "—"}`; `- Codings: {code.codings.count()}`.
- `## Last checked`: `Last checked for new matches: {max last_checked_at or "never"}`.
- Helpers `_describe_spec(spec)` and `_describe_lens(lens_dict)` produce human-readable strings (text, meeting name, states, municipalities, date range, document type). Use `QuerySpec.from_canonical` for queries; for lens dicts, read `text`, `states`, etc. directly. Return "No conditions" for an empty spec.

- [ ] **Step 4: Add the URL and view**

Append to `research/urls.py`:

```python
path("<uuid:project>/methods/", views.MethodsView.as_view(), name="methods"),
```

Add to `research/views.py`:

```python
class MethodsView(LoginRequiredMixin, View):
    template_name = "research/methods.html"

    def get(self, request, project):
        project_obj = get_object_or_404(Project, id=project, owner=request.user)
        markdown = build_methods(project_obj)
        return render(
            request,
            self.template_name,
            {"project": project_obj, "methods_markdown": markdown},
        )
```

- [ ] **Step 5: Create the template and add the link**

`templates/research/methods.html` extends `base.html`; breadcrumb Research / project / Methods; a "Copy Markdown" button using a small Alpine or plain JS `navigator.clipboard.writeText` on a hidden textarea; `<pre>` with `{{ methods_markdown }}`. Add a "Methods" link to `project_detail.html`.

- [ ] **Step 6: Run the tests and confirm they pass**

Run: `uv run --group test pytest tests/research/test_methods.py -q --no-cov`, then the full `tests/research` suite and `mypy research tests/research`.

- [ ] **Step 7: Commit**

```bash
git add research/methods.py research/views.py research/urls.py templates/research tests/research/test_methods.py
git commit -m "feat(research): add how-we-did-this methods summary"
```

---

### Task 6: CSV export

**Files:**
- Create: `research/export.py`
- Modify: `research/views.py` (`ExportView`)
- Modify: `research/urls.py`
- Modify: `research/tasks.py` (`export_lens_task`)
- Modify: `config/settings/base.py` (`RESEARCH_INLINE_EXPORT_MAX_ROWS`)
- Create: `templates/research/export.html`
- Modify: `templates/research/set_detail.html` (Export link)
- Test: `tests/research/test_export.py`

**Interfaces:**
- Consumes: `ResultSet`, `SetItem`, `MeetingPage`, `Coding`, `Code`, `Memo`, `Lens`, `filter_items`, `build_methods`, `csv`, `zipfile`, `io`, `django_rq`.
- Produces:
  - `export_rows(project, result_set, lens) -> Iterator[dict]`
  - `export_csv(project, result_set, lens) -> Iterator[str]`
  - `export_codebook_csv(project) -> Iterator[str]`
  - `export_zip(project, result_set, lens) -> bytes`
  - `export_lens_task(result_set_id, lens_canonical, user_id=None) -> str`
  - `ExportView` (GET `/research/<project>/sets/<set>/export/`)

- [ ] **Step 1: Write the failing export tests**

Create `tests/research/test_export.py`:

```python
import csv
import io
import zipfile

import pytest
from django.urls import reverse

from research.export import (
    export_codebook_csv,
    export_csv,
    export_rows,
    export_zip,
)
from research.lens import Lens
from research.models import SetItem
from tests.factories import (
    CodeFactory,
    CodingFactory,
    MeetingPageFactory,
    MemoFactory,
    MuniFactory,
    ProjectFactory,
    ResultSetFactory,
    SetItemFactory,
    UserFactory,
)


def _project_with_items():
    project = ProjectFactory()
    muni = MuniFactory(subdomain="berkeley", name="Berkeley", state="CA")
    document = MeetingPageFactory(
        document__municipality=muni,
        document__document_type="agenda",
        document__meeting_name="CityCouncil",
        page_number=3,
        text="The council approved the housing plan.",
    )
    result_set = ResultSetFactory(project=project, name="Snapshot")
    item = SetItemFactory(
        result_set=result_set,
        page=document,
        state="CA",
        document_type="agenda",
        disposition="r",
    )
    return project, result_set, item


@pytest.mark.django_db
class TestExportRows:
    def test_columns_and_values(self):
        project, result_set, item = _project_with_items()
        code = CodeFactory(project=project, name="Housing")
        CodingFactory(project=project, code=code, page=item.page)
        MemoFactory(project=project, page=item.page, body="Worth revisiting")

        rows = list(export_rows(project, result_set, Lens()))

        assert len(rows) == 1
        row = rows[0]
        assert list(row.keys()) == [
            "page_id",
            "civic_band_url",
            "municipality",
            "state",
            "meeting_name",
            "meeting_date",
            "document_type",
            "page_number",
            "disposition",
            "ignore_reason",
            "codes",
            "memo",
            "snippet",
        ]
        assert row["page_id"] == item.page_id
        assert row["civic_band_url"] == (
            "https://berkeley.civic.band/meetings/agendas/" + item.page_id
        )
        assert row["municipality"] == "Berkeley"
        assert row["state"] == "CA"
        assert row["meeting_name"] == "CityCouncil"
        assert row["document_type"] == "agenda"
        assert row["page_number"] == 3
        assert row["disposition"] == "Relevant"
        assert row["ignore_reason"] == ""
        assert row["codes"] == "Housing"
        assert row["memo"] == "Worth revisiting"
        assert "housing plan" in row["snippet"]

    def test_uses_item_denormalized_fields_when_page_is_missing(self):
        project = ProjectFactory()
        result_set = ResultSetFactory(project=project)
        SetItem.objects.create(
            result_set=result_set,
            page_id="missing-page",
            state="OR",
            document_type="minutes",
        )

        rows = list(export_rows(project, result_set, Lens()))

        assert len(rows) == 1
        assert rows[0]["page_id"] == "missing-page"
        assert rows[0]["state"] == "OR"
        assert rows[0]["document_type"] == "minutes"
        assert rows[0]["civic_band_url"] == ""


@pytest.mark.django_db
class TestExportCsv:
    def test_writes_the_header_and_rows(self):
        project, result_set, _item = _project_with_items()

        lines = list(export_csv(project, result_set, Lens()))
        parsed = list(csv.reader(io.StringIO("".join(lines))))

        assert parsed[0][0] == "page_id"
        assert parsed[1][0] == _item.page_id

    def test_codebook_csv_includes_definitions_and_counts(self):
        project = ProjectFactory()
        code = CodeFactory(project=project, name="Housing", definition="Housing policy")
        CodingFactory(project=project, code=code, page=MeetingPageFactory())

        lines = list(export_codebook_csv(project))
        parsed = list(csv.reader(io.StringIO("".join(lines))))

        assert parsed[0] == [
            "code",
            "parent",
            "definition",
            "apply_when",
            "do_not_apply_when",
            "coding_count",
        ]
        assert parsed[1][0] == "Housing"
        assert parsed[1][2] == "Housing policy"
        assert parsed[1][5] == "1"


@pytest.mark.django_db
class TestExportZip:
    def test_zip_contains_csv_codebook_and_methods(self):
        project, result_set, _item = _project_with_items()

        payload = export_zip(project, result_set, Lens())
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            names = set(archive.namelist())
            assert names == {"export.csv", "codebook.csv", "methods.md"}
            assert "page_id" in archive.read("export.csv").decode()
            assert "How we did this" in archive.read("methods.md").decode()


@pytest.mark.django_db
class TestExportView:
    def test_requires_login(self, client):
        project = ProjectFactory()
        result_set = ResultSetFactory(project=project)
        response = client.get(
            reverse("research:set-export", args=[project.id, result_set.id])
        )
        assert response.status_code == 302

    def test_cannot_export_another_users_set(self, client):
        user = UserFactory()
        client.force_login(user)
        project = ProjectFactory()
        result_set = ResultSetFactory(project=project)
        response = client.get(
            reverse("research:set-export", args=[project.id, result_set.id])
        )
        assert response.status_code == 404

    def test_streams_a_zip_for_small_lenses(self, client):
        user = UserFactory()
        client.force_login(user)
        project = ProjectFactory(owner=user)
        result_set = ResultSetFactory(project=project)
        SetItemFactory(result_set=result_set, page=MeetingPageFactory())

        response = client.get(
            reverse("research:set-export", args=[project.id, result_set.id])
        )

        assert response.status_code == 200
        assert response["Content-Type"] == "application/zip"
        assert "attachment" in response["Content-Disposition"]
        payload = b"".join(response.streaming_content)
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            assert "export.csv" in archive.namelist()

    def test_queues_a_job_for_large_lenses(self, client, settings):
        settings.RESEARCH_INLINE_EXPORT_MAX_ROWS = 1
        user = UserFactory()
        client.force_login(user)
        project = ProjectFactory(owner=user)
        result_set = ResultSetFactory(project=project)
        SetItemFactory(result_set=result_set, page=MeetingPageFactory())
        SetItemFactory(result_set=result_set, page=MeetingPageFactory())

        response = client.get(
            reverse("research:set-export", args=[project.id, result_set.id])
        )

        assert response.status_code == 202
        assert "Export queued" in response.content.decode()
        assert "job=" in response.content.decode()
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `uv run --group test pytest tests/research/test_export.py -q --no-cov`

Expected: `ModuleNotFoundError: No module named 'research.export'`.

- [ ] **Step 3: Implement `research/export.py`**

Write the module:

- `COLUMNS = ["page_id", "civic_band_url", "municipality", "state", "meeting_name", "meeting_date", "document_type", "page_number", "disposition", "ignore_reason", "codes", "memo", "snippet"]`.
- `export_rows(project, result_set, lens)`:
  - Iterate `filter_items(result_set, lens).select_related("last_event").order_by("id").iterator(chunk_size=1000)`.
  - Hydrate pages with `MeetingPage.objects.in_bulk([item.page_id for item in batch])` in batches of 1000. Simpler: for each item, use `MeetingPage.objects.in_bulk([item.page_id])`? That is N+1. Use a batch generator: yield items in chunks of 1000, hydrate, then yield rows. The function is a generator; maintain a buffer. Use `itertools.islice` or a helper.
  - For each item, build the row:
    - `page_id = item.page_id`
    - `page = pages.get(item.page_id)`
    - `civic_band_url = f"https://{page.municipality_subdomain}.civic.band/meetings/{page.civic_band_table_name}/{page.id}"` if page else `""`
    - `municipality = page.municipality_name if page else ""`
    - `state = item.state`
    - `meeting_name = page.meeting_name if page else ""`
    - `meeting_date = item.meeting_date.isoformat() if item.meeting_date else ""`
    - `document_type = item.document_type`
    - `page_number = page.page_number if page else ""`
    - `disposition = item.get_disposition_display()`
    - `ignore_reason = item.ignore_reason`
    - `codes = ";".join(Coding.objects.filter(project=project, page_id=item.page_id).values_list("code__name", flat=True).order_by("code__name"))` — this is N+1. Prefetch codings for the batch: `Coding.objects.filter(project=project, page_id__in=batch_page_ids).values_list("page_id", "code__name")`; group into a dict. Use `defaultdict(list)`. Sort names.
    - `memo = Memo.objects.filter(project=project, page_id=item.page_id).order_by("-created").values_list("body", flat=True).first() or ""` — N+1. Prefetch memos for the batch: `Memo.objects.filter(project=project, page_id__in=batch_page_ids).order_by("page_id", "-created")`; group; take first per page. Use a dict.
    - `snippet = (page.text if page else "")[:500]`
  - Yield rows.
- `export_csv(project, result_set, lens)`:
  - Use `io.StringIO` and `csv.DictWriter`; yield the header line first, then each row. Because `csv.DictWriter` writes to a file-like object, use a fresh `StringIO` per row or write to a buffer and yield `getvalue()`; simpler: `writer = csv.DictWriter(buffer, fieldnames=COLUMNS); writer.writeheader(); yield buffer.getvalue(); buffer.seek(0); buffer.truncate(0)`; for each row, `writer.writerow(row); yield buffer.getvalue(); ...`. Or use `csv.writer` with `writerow([row[column] for column in COLUMNS])`. The `csv` module writes to a buffer. Use a helper `_csv_line(values)` that returns a string. Simplest: `buffer = io.StringIO(); writer = csv.writer(buffer); writer.writerow(COLUMNS); yield buffer.getvalue(); for row in export_rows(...): buffer.seek(0); buffer.truncate(0); writer.writerow([row[column] for column in COLUMNS]); yield buffer.getvalue()`.
- `export_codebook_csv(project)`:
  - Header `["code", "parent", "definition", "apply_when", "do_not_apply_when", "coding_count"]`; for each non-archived code ordered by name, `[code.name, code.parent.name if code.parent else "", code.definition, code.inclusion, code.exclusion, code.codings.count()]`.
- `export_zip(project, result_set, lens)`:
  - `buffer = io.BytesIO()`; `with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:`; `archive.writestr("export.csv", "".join(export_csv(...)))`; `archive.writestr("codebook.csv", "".join(export_codebook_csv(project)))`; `archive.writestr("methods.md", build_methods(project))`; return `buffer.getvalue()`.
- `export_lens_task(result_set_id, lens_canonical, user_id=None)`:
  - `result_set = ResultSet.objects.get(pk=result_set_id)`; `lens = Lens.from_canonical(lens_canonical)` (add `Lens.from_canonical` if missing; the `Lens` class has `canonical()` but maybe not `from_canonical`; add a classmethod that reconstructs from the dict, or use `Lens(**dict)` with date parsing. Simpler: the view passes `lens.canonical()`; the task reconstructs with `Lens.from_canonical(lens_canonical)`. Add `Lens.from_canonical` in `research/lens.py` mirroring `canonical()`.
  - Build the zip; write it to `settings.MEDIA_ROOT / "research_exports" / f"{job_id}.zip"`; return the path. Get the job id via `rq.get_current_job().id` when available; fall back to `uuid4()`.
  - The task returns the path. The view's `?job=` branch reads the job result.

- [ ] **Step 4: Add the URL and view**

Append to `research/urls.py`:

```python
path(
    "<uuid:project>/sets/<uuid:set>/export/",
    views.ExportView.as_view(),
    name="set-export",
),
```

Add to `research/views.py`:

```python
class ExportView(LoginRequiredMixin, View):
    def get(self, request, project, set):
        result_set = get_object_or_404(
            ResultSet, id=set, project_id=project, project__owner=request.user
        )
        lens = Lens.from_querydict(request.GET)
        job_id = request.GET.get("job")
        if job_id:
            return self._job_response(request, result_set, job_id)
        count = filter_items(result_set, lens).count()
        if count <= settings.RESEARCH_INLINE_EXPORT_MAX_ROWS:
            payload = export_zip(result_set.project, result_set, lens)
            response = StreamingHttpResponse(
                iter([payload]), content_type="application/zip"
            )
            response["Content-Disposition"] = (
                f'attachment; filename="{_export_filename(result_set)}"'
            )
            return response
        job = _enqueue_export(result_set, lens, request.user)
        return render(
            request,
            "research/export.html",
            {"result_set": result_set, "job_id": job.id, "count": count},
            status=202,
        )
```

Add helpers `_enqueue_export`, `_export_filename`, `_job_response`:
- `_enqueue_export`: `queue = django_rq.get_queue(settings.RESEARCH_QUEUE); return queue.enqueue(export_lens_task, result_set.id, lens.canonical(), request.user.id, job_timeout=settings.RESEARCH_JOB_TIMEOUT)`.
- `_export_filename`: slugify the set name and append `-export.zip`.
- `_job_response`: `job = django_rq.get_queue(settings.RESEARCH_QUEUE).fetch_job(job_id)`; if `job is None`, 404; check `str(job.args[0]) == str(result_set.id)` to prevent cross-user job access; if `job.is_failed`, return 500; if not `job.is_finished`, render `export.html` with status 202 and "Still generating"; if finished, `return FileResponse(open(job.result, "rb"), as_attachment=True, filename=...)`.

- [ ] **Step 5: Add the setting**

In `config/settings/base.py`, after `RESEARCH_USE_SQL_MATERIALIZATION`, add:

```python
RESEARCH_INLINE_EXPORT_MAX_ROWS = env.int("RESEARCH_INLINE_EXPORT_MAX_ROWS", 10_000)
```

- [ ] **Step 6: Add the template and link**

`templates/research/export.html` extends `base.html`; shows "Export queued" and a link to the same URL with `?job={{ job_id }}`; the link text "Download when ready". Add an "Export CSV" link to `set_detail.html` with the current `querystring`.

- [ ] **Step 7: Run the tests and confirm they pass**

Run: `uv run --group test pytest tests/research/test_export.py -q --no-cov`, then the full `tests/research` suite and `mypy research tests/research`.

- [ ] **Step 8: Commit**

```bash
git add research/export.py research/views.py research/urls.py research/tasks.py research/lens.py config/settings/base.py templates/research tests/research/test_export.py
git commit -m "feat(research): add CSV export with job-backed large exports"
```

---

## Final Review Focus (for the whole-branch reviewer)

Check each of these against the diff and the tests:

- Codebook tree, create/rename/archive/merge are project-scoped and one-level; merge moves codings and deletes the source; cross-project operations 404 or raise.
- Apply/remove code writes `items.coded`/`items.uncoded`, ignores duplicates, skips missing pages, and never touches another project.
- Memo targets belong to the project; more than one target is a 400; the timeline interleaves memos and events in date order; memo Markdown is escaped for HTML.
- The methods summary is generated from events and current items; it includes each query with date and count, each set's funnel, the codebook with counts and definitions, and the last new-match check.
- The export header order matches the spec; every lens row is included; `codebook.csv` and `methods.md` are in the zip; the civic.band URL is raw; large exports return a job-backed 202 and a download link, never a truncated stream.
- Every new view is owner-scoped, POST + CSRF for mutations, and 404s for another user's project/set/code/memo.
- No stubs for notebook migration/redirects or the navigation/homepage/search/clip swaps.
