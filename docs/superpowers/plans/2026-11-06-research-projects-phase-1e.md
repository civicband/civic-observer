# Research Projects — Phase 1E Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Notebooks with Research Projects in the product surface: migrate notebook data into research projects, redirect the old notebook URLs, add "Add to set" and "Save as set" entry points from search and clipping, and point navigation/homepage at `/research/`.

**Architecture:** A `RunPython` migration in `research` maps each notebook to a `Project` with the same UUID and its entries to one "Saved pages" `ResultSet` (`origin=IMPORT`), notes to page `Memo`s, and tags to project `Code`s/`Coding`s. A read-only management command verifies the counts. Permanent redirects replace the notebook list/detail URLs. New HTMX views in `research` render "Add to set" (single page) and "Save as set" (whole search) panels; the search results partial and `clip/views.py` call those views instead of notebook views. Navigation and homepage copy point at `/research/`.

**Tech Stack:** Django 5.x, PostgreSQL/ParadeDB, HTMX, Alpine, Django templates, pytest.

**Spec:** `docs/superpowers/specs/2026-11-06-research-projects-design.md` (sections "Notebooks migration and removal", "Entry points from search", "URLs, views and UI")

## Global Constraints

- All new views are `LoginRequiredMixin` and filter through `project__owner=request.user`; other users' projects/sets 404.
- Mutations require POST + CSRF. HTMX mutations return partials; non-HTMX POSTs redirect.
- Every membership/classification change writes one append-only `AuditEvent`.
- A missing page (`db_constraint=False`) must not 500; use the denormalized `SetItem` fields.
- The migration is idempotent: `get_or_create` keyed on the notebook id; re-running writes no duplicate rows or events.
- The migration's reverse deletes only projects whose ids match migrated notebook ids.
- No set over `RESEARCH_SET_MAX_ITEMS` is created; `TOO_LARGE` sets keep their error and are not retried automatically.
- Notebook views stay reachable only through redirects after this phase; the `notebooks` app stays installed until Release 2.
- Do not delete the `notebooks` app or its tables in this phase.
- Do not add a dependency; `markdown-it-py` is already installed.

## Review Focus

1. Re-running the migration must not duplicate projects, sets, items, memos, codes, codings, or `project.imported` events (Task 1).
2. An entry whose `MeetingPage` no longer exists must migrate without a 500 and must not lose its note/tags (Task 1).
3. A notebook with zero entries, zero notes, or zero tags must still become a project with an empty "Saved pages" set and no phantom events (Task 1).
4. "Add to set" must default to the most recently used set, create "My research" / "Saved pages" when the user has none, and never add a page twice to the same set (Task 4).
5. "Save as set" must show a capped count, refuse to submit over `RESEARCH_SET_MAX_ITEMS`, and create the project when the user picks "new" (Task 5).
6. `clip/views.py` must add to a research set, not a notebook, and must not 500 when the page is already present (Task 6).
7. Old `/notebooks/` and `/notebooks/<uuid>/` links must 301/302 to `/research/` and `/research/<uuid>/`, and the destination must 404 for a non-owner (Task 3).

---

### Task 1: Notebook → Research data migration

**Files:**
- Create: `research/notebooks_migration.py`
- Create: `research/migrations/0004_migrate_notebooks.py`
- Test: `tests/research/test_notebooks_migration.py`

**Interfaces:**
- Consumes: `Project`, `ResultSet`, `SetItem`, `Memo`, `Code`, `Coding`, `AuditEvent`, `record_event`, `notebooks.Notebook`, `notebooks.NotebookEntry`, `notebooks.Tag`, `timezone`.
- Produces:
  - `migrate_notebooks(notebooks=None) -> dict[str, dict]` — keyed by notebook id; each value has `project`, `set`, `entries`, `notes`, `codes`, `codings`.
  - `migrate_notebook(notebook) -> dict`
  - `unmigrate_notebooks(notebook_ids=None) -> int`
  - Migration `0004_migrate_notebooks` depends on `notebooks.0003_notebookentry`.

- [ ] **Step 1: Write the failing migration tests**

Create `tests/research/test_notebooks_migration.py`:

```python
import pytest

from research.models import Code, Coding, Memo, Project, ResultSet, SetItem
from research.notebooks_migration import migrate_notebook, migrate_notebooks
from tests.factories import (
    MeetingDocumentFactory,
    MeetingPageFactory,
    NotebookEntryFactory,
    NotebookFactory,
    TagFactory,
    UserFactory,
)


@pytest.mark.django_db
class TestMigrateNotebook:
    def test_creates_project_with_same_uuid_and_saved_pages_set(self):
        notebook = NotebookFactory(name="Housing watch")

        result = migrate_notebook(notebook)

        project = Project.objects.get(id=notebook.id)
        assert project.owner == notebook.user
        assert project.name == "Housing watch"
        result_set = ResultSet.objects.get(project=project)
        assert result_set.name == "Saved pages"
        assert result_set.origin == ResultSet.Origin.IMPORT
        assert result_set.status == ResultSet.Status.READY
        assert result["project"] == project

    def test_maps_entries_notes_and_tags(self):
        notebook = NotebookFactory()
        page = MeetingPageFactory(document=MeetingDocumentFactory())
        tag = TagFactory(user=notebook.user, name="housing")
        entry = NotebookEntryFactory(
            notebook=notebook, meeting_page=page, note="Worth revisiting"
        )
        entry.tags.add(tag)

        migrate_notebook(notebook)

        project = Project.objects.get(id=notebook.id)
        item = SetItem.objects.get(result_set__project=project, page=page)
        assert item.disposition == SetItem.Disposition.RELEVANT
        assert item.added_via == SetItem.AddedVia.IMPORT
        assert item.added_at == entry.created
        memo = Memo.objects.get(project=project, page=page)
        assert memo.body == "Worth revisiting"
        code = Code.objects.get(project=project, name="housing")
        assert Coding.objects.filter(project=project, code=code, page=page).exists()

    def test_is_idempotent(self):
        notebook = NotebookFactory()
        page = MeetingPageFactory(document=MeetingDocumentFactory())
        NotebookEntryFactory(notebook=notebook, meeting_page=page, note="note")

        migrate_notebook(notebook)
        migrate_notebook(notebook)

        project = Project.objects.get(id=notebook.id)
        assert Project.objects.filter(id=notebook.id).count() == 1
        assert ResultSet.objects.filter(project=project).count() == 1
        assert SetItem.objects.filter(result_set__project=project).count() == 1
        assert Memo.objects.filter(project=project).count() == 1
        assert project.events.filter(verb="project.imported").count() == 1

    def test_empty_notebook_creates_empty_set_and_event(self):
        notebook = NotebookFactory()

        migrate_notebook(notebook)

        project = Project.objects.get(id=notebook.id)
        assert SetItem.objects.filter(result_set__project=project).count() == 0
        event = project.events.get(verb="project.imported")
        assert event.payload["entries"] == 0

    def test_missing_page_keeps_note_and_tag(self):
        notebook = NotebookFactory()
        page = MeetingPageFactory(document=MeetingDocumentFactory())
        tag = TagFactory(user=notebook.user, name="housing")
        entry = NotebookEntryFactory(notebook=notebook, meeting_page=page, note="note")
        entry.tags.add(tag)
        page_id = page.id
        page.delete()

        migrate_notebook(notebook)

        project = Project.objects.get(id=notebook.id)
        assert SetItem.objects.filter(
            result_set__project=project, page_id=page_id
        ).exists()
        assert Memo.objects.filter(project=project, page_id=page_id).exists()
        assert Coding.objects.filter(project=project, page_id=page_id).exists()


@pytest.mark.django_db
class TestMigrateNotebooks:
    def test_migrates_every_notebook_and_orphan_tags(self):
        user = UserFactory()
        first = NotebookFactory(user=user, name="First")
        second = NotebookFactory(user=user, name="Second")
        orphan = TagFactory(user=user, name="orphan")

        results = migrate_notebooks([first, second])

        assert set(results) == {str(first.id), str(second.id)}
        assert Project.objects.filter(id=first.id).exists()
        assert Project.objects.filter(id=second.id).exists()
        assert Code.objects.filter(project__owner=user, name="orphan").exists()
        assert not Coding.objects.filter(code__name="orphan").exists()
```

- [ ] **Step 2: Run the tests to confirm they fail**

Run: `uv run --group test pytest tests/research/test_notebooks_migration.py -q --no-cov`
Expected: `ModuleNotFoundError: No module named 'research.notebooks_migration'`.

- [ ] **Step 3: Implement `research/notebooks_migration.py`**

Write:

```python
"""Migrate Notebooks data into Research Projects (Release 1)."""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from .audit import record_event
from .models import Code, Coding, Memo, Project, ResultSet, SetItem

SAVED_PAGES = "Saved pages"


def _project_for_notebook(notebook):
    project, _created = Project.objects.get_or_create(
        id=notebook.id,
        defaults={
            "owner": notebook.user,
            "name": notebook.name,
            "is_archived": notebook.is_archived,
        },
    )
    return project


def _saved_pages_set(project):
    result_set, _created = ResultSet.objects.get_or_create(
        project=project,
        name=SAVED_PAGES,
        origin=ResultSet.Origin.IMPORT,
        defaults={
            "status": ResultSet.Status.READY,
            "materialized_at": timezone.now(),
        },
    )
    return result_set


def _migrate_entries(project, result_set, notebook):
    counts = {"entries": 0, "notes": 0, "codes": set(), "codings": 0}
    for entry in notebook.entries.select_related("meeting_page").prefetch_related(
        "tags"
    ):
        page = entry.meeting_page
        _item, created = SetItem.objects.get_or_create(
            result_set=result_set,
            page=page,
            defaults={
                "disposition": SetItem.Disposition.RELEVANT,
                "added_via": SetItem.AddedVia.IMPORT,
                "added_at": entry.created,
                "state": page.state,
                "document_type": page.document_type,
                "meeting_date": page.meeting_date,
                "municipality_id": page.municipality_id,
            },
        )
        if created:
            counts["entries"] += 1

        note = (entry.note or "").strip()
        if note:
            _memo, memo_created = Memo.objects.get_or_create(
                project=project,
                page=page,
                author=notebook.user,
                body=note,
            )
            if memo_created:
                counts["notes"] += 1

        for tag in entry.tags.all():
            code, _code_created = Code.objects.get_or_create(
                project=project,
                parent=None,
                name=tag.name,
            )
            _coding, coding_created = Coding.objects.get_or_create(
                project=project,
                code=code,
                page=page,
                span_start=None,
                span_end=None,
                defaults={"coder": notebook.user},
            )
            if coding_created:
                counts["codes"].add(code.id)
                counts["codings"] += 1
    return counts


def _code_orphan_tags(user):
    """Create codes for tags used nowhere in the user's most recent project."""
    from notebooks.models import Tag

    orphan_tags = Tag.objects.filter(user=user, entries__isnull=True)
    if not orphan_tags.exists():
        return 0
    project = (
        Project.objects.filter(owner=user, is_archived=False)
        .order_by("-modified")
        .first()
    )
    if project is None:
        project = Project.objects.create(owner=user, name="My research")
    created = 0
    for tag in orphan_tags:
        _code, was_created = Code.objects.get_or_create(
            project=project, parent=None, name=tag.name
        )
        if was_created:
            created += 1
    return created


@transaction.atomic
def migrate_notebook(notebook) -> dict:
    project = _project_for_notebook(notebook)
    result_set = _saved_pages_set(project)
    counts = _migrate_entries(project, result_set, notebook)
    result_set.item_count = SetItem.objects.filter(result_set=result_set).count()
    result_set.save(update_fields=["item_count", "modified"])

    if not project.events.filter(
        verb="project.imported", payload__notebook=str(notebook.id)
    ).exists():
        record_event(
            project,
            "project.imported",
            actor=notebook.user,
            result_set=result_set,
            payload={
                "notebook": str(notebook.id),
                "entries": counts["entries"],
                "notes": counts["notes"],
                "codes": len(counts["codes"]),
                "codings": counts["codings"],
            },
        )
    _code_orphan_tags(notebook.user)
    return {
        "project": project,
        "set": result_set,
        "entries": counts["entries"],
        "notes": counts["notes"],
        "codes": len(counts["codes"]),
        "codings": counts["codings"],
    }


def migrate_notebooks(notebooks=None) -> dict[str, dict]:
    from notebooks.models import Notebook

    if notebooks is None:
        notebooks = Notebook.objects.select_related("user").all()
    return {str(notebook.id): migrate_notebook(notebook) for notebook in notebooks}


def unmigrate_notebooks(notebook_ids=None) -> int:
    from notebooks.models import Notebook

    if notebook_ids is None:
        notebook_ids = Notebook.objects.values_list("id", flat=True)
    deleted, _details = Project.objects.filter(id__in=list(notebook_ids)).delete()
    return deleted
```

- [ ] **Step 4: Create the migration**

Create `research/migrations/0004_migrate_notebooks.py`:

```python
from django.db import migrations


def forwards(apps, schema_editor):
    from research.notebooks_migration import migrate_notebooks

    migrate_notebooks()


def backwards(apps, schema_editor):
    from research.notebooks_migration import unmigrate_notebooks

    unmigrate_notebooks()


class Migration(migrations.Migration):
    dependencies = [
        ("research", "0003_alter_memo_code_alter_memo_result_set"),
        ("notebooks", "0003_notebookentry"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
```

- [ ] **Step 5: Run the tests and confirm they pass**

Run: `uv run --group test pytest tests/research/test_notebooks_migration.py -q --no-cov`
Expected: PASS.

- [ ] **Step 6: Run the migration smoke test and commit**

Run:
```bash
uv run python manage.py migrate research 0004 --noinput
uv run python manage.py migrate research 0003 --noinput
uv run python manage.py migrate research 0004 --noinput
```
Expected: no errors; the second 0004 run is a no-op.

```bash
git add research/notebooks_migration.py research/migrations/0004_migrate_notebooks.py tests/research/test_notebooks_migration.py
git commit -m "feat(research): migrate notebooks into research projects"
```

---

### Task 2: Notebook migration verification command

**Files:**
- Create: `research/management/commands/verify_notebook_migration.py`
- Test: `tests/research/test_verify_notebook_migration.py`

**Interfaces:**
- Consumes: `Notebook`, `NotebookEntry`, `Project`, `ResultSet`, `SetItem`, `Memo`, `Coding`.
- Produces: `Command` (Django management command) with `--notebook <uuid>` optional filter; prints a table and exits non-zero on mismatch.

- [ ] **Step 1: Write the failing command tests**

Create `tests/research/test_verify_notebook_migration.py`:

```python
import pytest
from django.core.management import call_command

from research.notebooks_migration import migrate_notebook
from tests.factories import (
    MeetingDocumentFactory,
    MeetingPageFactory,
    NotebookEntryFactory,
    NotebookFactory,
    TagFactory,
)


@pytest.mark.django_db
class TestVerifyNotebookMigration:
    def test_reports_matching_counts(self, capsys):
        notebook = NotebookFactory(name="Housing")
        page = MeetingPageFactory(document=MeetingDocumentFactory())
        tag = TagFactory(user=notebook.user, name="housing")
        entry = NotebookEntryFactory(notebook=notebook, meeting_page=page, note="note")
        entry.tags.add(tag)
        migrate_notebook(notebook)

        call_command("verify_notebook_migration", stdout=open("/dev/null", "w"))

        captured = capsys.readouterr()
        assert "Housing" in captured.out
        assert "OK" in captured.out

    def test_reports_missing_project(self, capsys):
        NotebookFactory(name="Orphan")

        with pytest.raises(SystemExit):
            call_command("verify_notebook_migration")

        captured = capsys.readouterr()
        assert "MISSING" in captured.out
        assert "Orphan" in captured.out
```

- [ ] **Step 2: Run the tests to confirm they fail**

Run: `uv run --group test pytest tests/research/test_verify_notebook_migration.py -q --no-cov`
Expected: `CommandError: Unknown command: 'verify_notebook_migration'`.

- [ ] **Step 3: Implement the command**

Create `research/management/commands/verify_notebook_migration.py`:

```python
"""Compare Notebooks counts against the migrated Research Projects."""

from django.core.management.base import BaseCommand

from notebooks.models import Notebook
from research.models import Coding, Memo, Project, ResultSet, SetItem


class Command(BaseCommand):
    help = "Verify notebook entry, note and tag counts against migrated projects."

    def add_arguments(self, parser):
        parser.add_argument("--notebook", dest="notebook", default=None)

    def handle(self, *args, **options):
        notebooks = Notebook.objects.all().order_by("name")
        if options["notebook"]:
            notebooks = notebooks.filter(id=options["notebook"])

        failures = 0
        for notebook in notebooks:
            project = Project.objects.filter(id=notebook.id).first()
            if project is None:
                failures += 1
                self.stdout.write(
                    self.style.ERROR(f"MISSING {notebook.name} ({notebook.id})")
                )
                continue

            result_set = ResultSet.objects.filter(
                project=project, origin=ResultSet.Origin.IMPORT
            ).first()
            entry_count = notebook.entries.count()
            note_count = notebook.entries.exclude(note="").count()
            tag_count = sum(entry.tags.count() for entry in notebook.entries.all())
            setitem_count = (
                SetItem.objects.filter(result_set=result_set).count()
                if result_set
                else 0
            )
            memo_count = Memo.objects.filter(project=project).count()
            coding_count = Coding.objects.filter(project=project).count()

            mismatches = []
            if setitem_count != entry_count:
                mismatches.append(f"entries {setitem_count}/{entry_count}")
            if memo_count != note_count:
                mismatches.append(f"notes {memo_count}/{note_count}")
            if coding_count != tag_count:
                mismatches.append(f"tags {coding_count}/{tag_count}")
            if mismatches:
                failures += 1
                self.stdout.write(
                    self.style.WARNING(
                        f"MISMATCH {notebook.name} ({notebook.id}): "
                        + ", ".join(mismatches)
                    )
                )
            else:
                self.stdout.write(
                    f"OK {notebook.name} ({notebook.id}): "
                    f"{entry_count} entries, {note_count} notes, {tag_count} tags"
                )

        if failures:
            self.stderr.write(f"{failures} notebook(s) failed verification")
            raise SystemExit(1)
        self.stdout.write(self.style.SUCCESS("All notebooks verified"))
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `uv run --group test pytest tests/research/test_verify_notebook_migration.py -q --no-cov`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add research/management/commands/verify_notebook_migration.py tests/research/test_verify_notebook_migration.py
git commit -m "feat(research): verify notebook migration counts"
```

---

### Task 3: Permanent notebook redirects

**Files:**
- Modify: `config/urls.py`
- Modify: `notebooks/urls.py` (keep non-list/detail routes reachable; redirect the list/detail)
- Test: `tests/research/test_notebook_redirects.py`

**Interfaces:**
- Consumes: `RedirectView`, `research:project-list`, `research:project-detail`.
- Produces: `/notebooks/` → `/research/` and `/notebooks/<uuid>/` → `/research/<uuid>/`, both permanent.

- [ ] **Step 1: Write the failing redirect tests**

Create `tests/research/test_notebook_redirects.py`:

```python
import pytest
from django.urls import reverse

from tests.factories import ProjectFactory, UserFactory


@pytest.mark.django_db
class TestNotebookRedirects:
    def test_list_redirects_to_research(self, client):
        response = client.get("/notebooks/")
        assert response.status_code == 301
        assert response.url == "/research/"

    def test_detail_redirects_to_project(self, client):
        user = UserFactory()
        project = ProjectFactory(owner=user)
        client.force_login(user)

        response = client.get(f"/notebooks/{project.id}/")

        assert response.status_code == 301
        assert response.url == reverse("research:project-detail", args=[project.id])

    def test_redirect_destination_404s_for_non_owner(self, client):
        project = ProjectFactory()
        client.force_login(UserFactory())

        response = client.get(reverse("research:project-detail", args=[project.id]))
        assert response.status_code == 404
```

- [ ] **Step 2: Run the tests to confirm they fail**

Run: `uv run --group test pytest tests/research/test_notebook_redirects.py -q --no-cov`
Expected: FAIL — `/notebooks/` renders the notebook list, not a redirect.

- [ ] **Step 3: Implement the redirects**

In `config/urls.py`, replace the `notebooks` include with redirects **before** the research include:

```python
from django.views.generic import RedirectView

urlpatterns = [
    path(
        "notebooks/",
        RedirectView.as_view(url="/research/", permanent=True),
        name="notebooks-redirect",
    ),
    path(
        "notebooks/<uuid:project>/",
        RedirectView.as_view(pattern_name="research:project-detail", permanent=True),
        name="notebook-detail-redirect",
    ),
]
```

Keep the rest of the `notebooks.urls` routes (save-page, tags, entry edit/delete) reachable by including them under a non-conflicting prefix? The design only requires the list/detail redirects. Remove the `path("notebooks/", include("notebooks.urls"))` line so the two redirects own `/notebooks/` and `/notebooks/<uuid>/`.

Update `tests/notebooks/test_views.py` list/detail tests to expect 301 redirects (or move those assertions into the new redirect test and skip the old ones). Keep the save-page/tag/entry tests as-is by routing them through the notebook app's remaining URLconf under a separate prefix? If the notebook routes are removed entirely, those tests fail. Instead, keep the notebook app's routes but mount them at `/notebooks/legacy/` and add redirects for the public paths. That keeps the old tests runnable without exposing the old UI at the public URLs.

In `config/urls.py`:

```python
path("notebooks/legacy/", include("notebooks.urls")),
```

Then update `notebooks/urls.py` to be included with the `legacy` prefix (the app's `app_name` stays `notebooks`; reversing still works because the include is still present). The notebook tests keep reversing `notebooks:*` and now hit `/notebooks/legacy/...`, so they keep passing. The public `/notebooks/` and `/notebooks/<uuid>/` are redirects.

- [ ] **Step 4: Run the redirect tests and the notebook tests**

Run: `uv run --group test pytest tests/research/test_notebook_redirects.py tests/notebooks -q --no-cov`
Expected: redirect tests pass; notebook tests still pass against the legacy prefix.

- [ ] **Step 5: Commit**

```bash
git add config/urls.py notebooks/urls.py tests/research/test_notebook_redirects.py tests/notebooks
git commit -m "feat: redirect notebook URLs to research projects"
```

---

### Task 4: "Add to set" service and search results control

**Files:**
- Create: `research/add_to_set.py`
- Create: `research/views.py` (`AddToSetView`, GET/POST)
- Create: `templates/research/partials/add_to_set_button.html`
- Create: `templates/research/partials/add_to_set_panel.html`
- Modify: `research/urls.py`
- Modify: `templates/meetings/partials/search_results.html`
- Modify: `meetings/views.py`
- Test: `tests/research/test_add_to_set.py`
- Delete: `tests/notebooks/test_search_integration.py` (moved to `tests/research/test_search_integration.py`)

**Interfaces:**
- Consumes: `Project`, `ResultSet`, `SetItem`, `Memo`, `Code`, `Coding`, `record_event`, `MeetingPage`, `timezone`.
- Produces:
  - `default_set_for_user(user) -> ResultSet`
  - `add_page_to_set(project, result_set, page, *, actor, note="", code_names=None) -> SetItem`
  - `add_page_to_default_set(user, page, *, actor, note="", code_names=None) -> SetItem`
  - `AddToSetView` (GET/POST `/research/add-to-set/`) — GET renders the panel for `?page=`; POST adds the page and returns the confirmation.
  - Template partial `add_to_set_button.html` replaces the notebook save button in search results.

- [ ] **Step 1: Write the failing service tests**

Create `tests/research/test_add_to_set.py`:

```python
import pytest

from research.add_to_set import (
    add_page_to_default_set,
    add_page_to_set,
    default_set_for_user,
)
from research.models import Code, Coding, Memo, Project, ResultSet, SetItem
from tests.factories import (
    MeetingPageFactory,
    ProjectFactory,
    ResultSetFactory,
    SetItemFactory,
    UserFactory,
)


@pytest.mark.django_db
class TestDefaultSet:
    def test_uses_most_recently_modified_set(self):
        user = UserFactory()
        older = ResultSetFactory(project=ProjectFactory(owner=user))
        newer = ResultSetFactory(project=ProjectFactory(owner=user))

        assert default_set_for_user(user) == newer

    def test_creates_my_research_and_saved_pages_when_empty(self):
        user = UserFactory()

        result_set = default_set_for_user(user)

        assert result_set.name == "Saved pages"
        assert result_set.origin == ResultSet.Origin.MANUAL
        assert result_set.project.name == "My research"
        assert result_set.project.owner == user


@pytest.mark.django_db
class TestAddPageToSet:
    def test_adds_page_with_manual_added_via(self):
        user = UserFactory()
        project = ProjectFactory(owner=user)
        result_set = ResultSetFactory(project=project, origin=ResultSet.Origin.MANUAL)
        page = MeetingPageFactory()

        item = add_page_to_set(project, result_set, page, actor=user)

        assert item.added_via == SetItem.AddedVia.MANUAL
        assert item.disposition == SetItem.Disposition.UNREVIEWED
        event = project.events.get(verb="item.added")
        assert event.payload["page"] == page.id

    def test_does_not_duplicate_page(self):
        user = UserFactory()
        project = ProjectFactory(owner=user)
        result_set = ResultSetFactory(project=project, origin=ResultSet.Origin.MANUAL)
        page = MeetingPageFactory()
        SetItemFactory(result_set=result_set, page=page)

        add_page_to_set(project, result_set, page, actor=user)
        add_page_to_set(project, result_set, page, actor=user)

        assert SetItem.objects.filter(result_set=result_set, page=page).count() == 1
        assert project.events.filter(verb="item.added").count() == 0

    def test_note_becomes_page_memo_and_codes_become_codings(self):
        user = UserFactory()
        project = ProjectFactory(owner=user)
        result_set = ResultSetFactory(project=project, origin=ResultSet.Origin.MANUAL)
        page = MeetingPageFactory()

        add_page_to_set(
            project,
            result_set,
            page,
            actor=user,
            note="Follow up",
            code_names=["Housing", "Budget"],
        )

        assert Memo.objects.filter(
            project=project, page=page, body="Follow up"
        ).exists()
        for name in ("Housing", "Budget"):
            code = Code.objects.get(project=project, name=name)
            assert Coding.objects.filter(project=project, code=code, page=page).exists()


@pytest.mark.django_db
class TestAddPageToDefaultSet:
    def test_adds_to_created_default_set(self):
        user = UserFactory()
        page = MeetingPageFactory()

        item = add_page_to_default_set(user, page, actor=user)

        assert item.result_set.name == "Saved pages"
        assert item.result_set.project.name == "My research"
```

- [ ] **Step 2: Run the tests to confirm they fail**

Run: `uv run --group test pytest tests/research/test_add_to_set.py -q --no-cov`
Expected: `ModuleNotFoundError: No module named 'research.add_to_set'`.

- [ ] **Step 3: Implement `research/add_to_set.py`**

```python
"""Add single pages to research sets (the "Add to set" entry point)."""

from __future__ import annotations

from django.utils import timezone

from .audit import record_event
from .models import Code, Coding, Memo, Project, ResultSet, SetItem


def default_set_for_user(user) -> ResultSet:
    result_set = (
        ResultSet.objects.filter(project__owner=user, project__is_archived=False)
        .order_by("-modified")
        .first()
    )
    if result_set is not None:
        return result_set
    project = Project.objects.create(owner=user, name="My research")
    return ResultSet.objects.create(
        project=project,
        name="Saved pages",
        origin=ResultSet.Origin.MANUAL,
        status=ResultSet.Status.READY,
        materialized_at=timezone.now(),
    )


def add_page_to_set(
    project,
    result_set,
    page,
    *,
    actor,
    note: str = "",
    code_names: list[str] | None = None,
) -> SetItem:
    if result_set.project_id != project.id:
        raise ValueError("The set belongs to another project")
    item, created = SetItem.objects.get_or_create(
        result_set=result_set,
        page=page,
        defaults={
            "disposition": SetItem.Disposition.UNREVIEWED,
            "added_via": SetItem.AddedVia.MANUAL,
            "added_at": timezone.now(),
            "state": page.state,
            "document_type": page.document_type,
            "meeting_date": page.meeting_date,
            "municipality_id": page.municipality_id,
        },
    )
    if created:
        record_event(
            project,
            "item.added",
            actor=actor,
            result_set=result_set,
            payload={"page": page.id, "added_via": "manual"},
        )
        result_set.item_count = SetItem.objects.filter(result_set=result_set).count()
        result_set.save(update_fields=["item_count", "modified"])

    body = (note or "").strip()
    if body:
        Memo.objects.get_or_create(project=project, page=page, author=actor, body=body)
    for name in code_names or []:
        cleaned = name.strip()
        if not cleaned:
            continue
        code, _created = Code.objects.get_or_create(
            project=project, parent=None, name=cleaned
        )
        Coding.objects.get_or_create(
            project=project,
            code=code,
            page=page,
            span_start=None,
            span_end=None,
            defaults={"coder": actor},
        )
    return item


def add_page_to_default_set(
    user, page, *, actor, note: str = "", code_names: list[str] | None = None
) -> SetItem:
    result_set = default_set_for_user(user)
    return add_page_to_set(
        result_set.project,
        result_set,
        page,
        actor=actor,
        note=note,
        code_names=code_names,
    )
```

- [ ] **Step 4: Add the views and URLs**

In `research/views.py`, add a single view with GET and POST:

```python
class AddToSetView(LoginRequiredMixin, View):
    template_name = "research/partials/add_to_set_panel.html"

    def _context(self, request, page, **extra):
        default_set = default_set_for_user(request.user)
        projects = Project.objects.filter(
            owner=request.user, is_archived=False
        ).prefetch_related(
            "sets",
            Prefetch(
                "codes",
                queryset=Code.objects.filter(is_archived=False),
                to_attr="active_codes",
            ),
        )
        return {
            "page": page,
            "default_set": default_set,
            "projects": projects,
            "saved": SetItem.objects.filter(
                result_set__project__owner=request.user,
                result_set__project__is_archived=False,
                page=page,
            ).exists(),
            **extra,
        }

    def get(self, request):
        page = get_object_or_404(MeetingPage, id=request.GET.get("page"))
        return render(request, self.template_name, self._context(request, page))

    def post(self, request):
        page = get_object_or_404(MeetingPage, id=request.POST.get("page"))
        set_id = request.POST.get("set")
        if set_id:
            result_set = get_object_or_404(
                ResultSet, id=set_id, project__owner=request.user
            )
        else:
            result_set = default_set_for_user(request.user)
        add_page_to_set(
            result_set.project,
            result_set,
            page,
            actor=request.user,
            note=request.POST.get("note", ""),
            code_names=request.POST.getlist("codes"),
        )
        return render(
            request,
            self.template_name,
            self._context(request, page, saved=True, added_to=result_set),
        )
```

Add to `research/urls.py`:

```python
path("add-to-set/", views.AddToSetView.as_view(), name="add-to-set"),
```

Both GET (panel) and POST (add) use the same URL and name; the view branches on the method.

Create `templates/research/partials/add_to_set_button.html`:

```html
<button id="add-to-set-btn-{{ page_id }}"
        hx-get="{% url 'research:add-to-set' %}?page={{ page_id }}"
        hx-target="#add-to-set-panel-{{ page_id }}"
        hx-swap="outerHTML"
        class="inline-flex items-center px-3 py-1.5 border border-transparent text-xs font-medium rounded-md {% if is_saved %}text-indigo-600 bg-indigo-50 hover:bg-indigo-100{% else %}text-gray-600 bg-gray-100 hover:bg-gray-200{% endif %} transition-colors"
        title="{% if is_saved %}In a research set{% else %}Add to a research set{% endif %}">
    {% if is_saved %}In a set{% else %}Add to set{% endif %}
</button>
```

Create `templates/research/partials/add_to_set_panel.html`:

```html
<div id="add-to-set-panel-{{ page.id }}" class="mt-4 bg-gray-50 border border-gray-200 rounded-lg p-4">
    {% if saved %}
        <p class="text-sm text-gray-600">
            This page is already in a research set{% if added_to %} ({{ added_to.name }}){% endif %}.
        </p>
    {% else %}
        <form hx-post="{% url 'research:add-to-set' %}"
              hx-target="#add-to-set-panel-{{ page.id }}"
              hx-swap="outerHTML">
            {% csrf_token %}
            <input type="hidden" name="page" value="{{ page.id }}">
            <label for="add-set-{{ page.id }}" class="block text-sm font-medium text-gray-700 mb-1">
                Add to set
            </label>
            <select name="set" id="add-set-{{ page.id }}"
                    class="block w-full rounded-md border-gray-300 shadow-sm text-sm">
                <option value="{{ default_set.id }}">{{ default_set.project.name }} / {{ default_set.name }}</option>
                {% for project in projects %}
                    {% for result_set in project.sets.all %}
                        <option value="{{ result_set.id }}">{{ project.name }} / {{ result_set.name }}</option>
                    {% endfor %}
                {% endfor %}
            </select>
            <textarea name="note" rows="2" placeholder="Optional note"
                      class="mt-2 block w-full rounded-md border-gray-300 shadow-sm text-sm"></textarea>
            <select name="codes" multiple size="3"
                    class="mt-2 block w-full rounded-md border-gray-300 shadow-sm text-sm">
                {% for project in projects %}
                    {% for code in project.active_codes %}
                        <option value="{{ code.name }}">{{ project.name }} / {{ code.name }}</option>
                    {% endfor %}
                {% endfor %}
            </select>
            <button type="submit"
                    class="mt-2 inline-flex items-center px-3 py-1.5 border border-transparent text-xs font-medium rounded-md text-white bg-indigo-600 hover:bg-indigo-700">
                Add to set
            </button>
        </form>
    {% endif %}
</div>
```

Modify `templates/meetings/partials/search_results.html` to include the new control and panel placeholder:

```html
{% if user.is_authenticated %}
    {% include "research/partials/add_to_set_button.html" with page_id=result.id is_saved=result.id|in_set:saved_page_ids %}
{% endif %}
...
{% if user.is_authenticated %}
    <div id="add-to-set-panel-{{ result.id }}"></div>
{% endif %}
```

Modify `meetings/views.py` saved-page lookup:

```python
from research.models import SetItem as ResearchSetItem

saved_page_ids = set(
    ResearchSetItem.objects.filter(
        result_set__project__owner=request.user,
        result_set__project__is_archived=False,
        page_id__in=result_page_ids,
    ).values_list("page_id", flat=True)
)
```

- [ ] **Step 5: Write the view tests**

Add to `tests/research/test_add_to_set.py`:

```python
from django.urls import reverse

from tests.factories import (
    MeetingDocumentFactory,
    MeetingPageFactory,
    ProjectFactory,
    ResultSetFactory,
    SetItemFactory,
    UserFactory,
)


@pytest.mark.django_db
class TestAddToSetViews:
    def test_panel_requires_login(self, client):
        page = MeetingPageFactory(document=MeetingDocumentFactory())
        response = client.get(reverse("research:add-to-set"), {"page": page.id})
        assert response.status_code == 302

    def test_panel_lists_projects_and_sets(self, client):
        user = UserFactory()
        project = ProjectFactory(owner=user)
        result_set = ResultSetFactory(project=project, name="Saved pages")
        page = MeetingPageFactory(document=MeetingDocumentFactory())
        client.force_login(user)

        response = client.get(reverse("research:add-to-set"), {"page": page.id})

        content = response.content.decode()
        assert response.status_code == 200
        assert "Saved pages" in content
        assert str(result_set.id) in content

    def test_post_adds_page(self, client):
        user = UserFactory()
        project = ProjectFactory(owner=user)
        result_set = ResultSetFactory(project=project)
        page = MeetingPageFactory(document=MeetingDocumentFactory())
        client.force_login(user)

        response = client.post(
            reverse("research:add-to-set"),
            {"page": page.id, "set": str(result_set.id), "note": "note"},
        )

        assert response.status_code == 200
        assert SetItem.objects.filter(result_set=result_set, page=page).exists()

    def test_cannot_add_to_another_users_set(self, client):
        user = UserFactory()
        result_set = ResultSetFactory(project=ProjectFactory())
        page = MeetingPageFactory(document=MeetingDocumentFactory())
        client.force_login(user)

        response = client.post(
            reverse("research:add-to-set"),
            {"page": page.id, "set": str(result_set.id)},
        )

        assert response.status_code == 404
```

- [ ] **Step 6: Update the search integration test**

Replace `tests/notebooks/test_search_integration.py` with `tests/research/test_search_integration.py` to assert the new "Add to set" control and that `saved_page_ids` comes from `SetItem`:

```python
import pytest
from django.urls import reverse

from research.models import SetItem
from tests.factories import (
    MeetingDocumentFactory,
    MeetingPageFactory,
    MuniFactory,
    ProjectFactory,
    ResultSetFactory,
    SetItemFactory,
    UserFactory,
)


@pytest.mark.django_db
class TestSearchResultsAddToSet:
    def test_add_to_set_button_appears_for_authenticated_users(self, client):
        user = UserFactory()
        muni = MuniFactory()
        MeetingPageFactory(
            document=MeetingDocumentFactory(municipality=muni),
            text="housing policy discussion",
        )
        client.force_login(user)

        response = client.get(
            reverse("meetings:meeting-search-results"),
            {"query": "housing"},
            HTTP_HX_REQUEST="true",
        )

        assert response.status_code == 200
        assert "Add to set" in response.content.decode()

    def test_saved_state_uses_research_set_items(self, client):
        user = UserFactory()
        project = ProjectFactory(owner=user)
        result_set = ResultSetFactory(project=project)
        muni = MuniFactory()
        page = MeetingPageFactory(
            document=MeetingDocumentFactory(municipality=muni),
            text="housing policy discussion",
        )
        SetItemFactory(result_set=result_set, page=page)
        client.force_login(user)

        response = client.get(
            reverse("meetings:meeting-search-results"),
            {"query": "housing"},
            HTTP_HX_REQUEST="true",
        )

        content = response.content.decode()
        assert "In a set" in content
        assert SetItem.objects.filter(page=page).exists()
```

- [ ] **Step 7: Run the tests and commit**

Run: `uv run --group test pytest tests/research/test_add_to_set.py tests/notebooks/test_search_integration.py -q --no-cov`
Expected: PASS.

```bash
git add research/add_to_set.py research/views.py research/urls.py templates/research/partials/add_to_set_button.html templates/research/partials/add_to_set_panel.html templates/meetings/partials/search_results.html meetings/views.py tests/research/test_add_to_set.py tests/notebooks/test_search_integration.py
git commit -m "feat(research): add pages to research sets from search results"
```

---

### Task 5: "Save as set" panel on the search page

**Files:**
- Create: `research/projects.py` (`create_project`, `project_list_for_user`)
- Create: `research/views.py` (`ProjectCreateView`, `SaveAsSetView`)
- Create: `templates/research/partials/save_as_set_panel.html`
- Modify: `research/urls.py`
- Modify: `templates/meetings/meeting_search.html`
- Test: `tests/research/test_save_as_set.py`

**Interfaces:**
- Consumes: `Project`, `ResultSet`, `create_result_set`, `QuerySpec`, `search_backends`, `settings.RESEARCH_SET_MAX_ITEMS`, `count_matches`.
- Produces:
  - `create_project(user, name, description="") -> Project`
  - `ProjectCreateView` (POST `/research/projects/new/`)
  - `SaveAsSetView` (GET/POST `/research/save-as-set/`) — GET renders the panel with the capped count; POST creates a project if requested and a set, then redirects to the set detail.

- [ ] **Step 1: Write the failing tests**

Create `tests/research/test_save_as_set.py`:

```python
import pytest
from django.urls import reverse

from research.models import Project, ResultSet
from research.projects import create_project
from tests.factories import (
    MeetingDocumentFactory,
    MeetingPageFactory,
    MuniFactory,
    ProjectFactory,
    UserFactory,
)


@pytest.mark.django_db
class TestCreateProject:
    def test_creates_project_for_user(self):
        user = UserFactory()

        project = create_project(user, "Housing watch", "California")

        assert project.owner == user
        assert project.name == "Housing watch"
        assert project.description == "California"


@pytest.mark.django_db
class TestSaveAsSetPanel:
    def test_requires_login(self, client):
        response = client.get(reverse("research:save-as-set"), {"query": "housing"})
        assert response.status_code == 302

    def test_shows_capped_count_and_projects(self, client):
        user = UserFactory()
        ProjectFactory(owner=user, name="Existing")
        muni = MuniFactory()
        MeetingPageFactory(
            document=MeetingDocumentFactory(municipality=muni),
            text="housing policy",
        )
        client.force_login(user)

        response = client.get(reverse("research:save-as-set"), {"query": "housing"})

        content = response.content.decode()
        assert response.status_code == 200
        assert "Existing" in content
        assert "match" in content.lower()

    def test_over_ceiling_hides_submit(self, client, settings):
        settings.RESEARCH_SET_MAX_ITEMS = 0
        user = UserFactory()
        MeetingPageFactory(
            document=MeetingDocumentFactory(),
            text="housing policy",
        )
        client.force_login(user)

        response = client.get(reverse("research:save-as-set"), {"query": "housing"})

        content = response.content.decode()
        assert response.status_code == 200
        assert "too many" in content.lower() or "narrow" in content.lower()
        assert 'type="submit"' not in content


@pytest.mark.django_db
class TestSaveAsSetView:
    def test_creates_set_in_existing_project(self, client):
        user = UserFactory()
        project = ProjectFactory(owner=user)
        MeetingPageFactory(document=MeetingDocumentFactory(), text="housing policy")
        client.force_login(user)

        response = client.post(
            reverse("research:save-as-set"),
            {
                "project": str(project.id),
                "name": "Housing",
                "q": "housing",
            },
        )

        result_set = ResultSet.objects.get(project=project, name="Housing")
        assert response.status_code == 302
        assert response.url == reverse(
            "research:set-detail", args=[project.id, result_set.id]
        )

    def test_creates_project_when_new(self, client):
        user = UserFactory()
        MeetingPageFactory(document=MeetingDocumentFactory(), text="housing policy")
        client.force_login(user)

        response = client.post(
            reverse("research:save-as-set"),
            {"new_project": "Housing watch", "name": "Housing", "q": "housing"},
        )

        project = Project.objects.get(owner=user, name="Housing watch")
        assert ResultSet.objects.filter(project=project, name="Housing").exists()
        assert response.status_code == 302
```

- [ ] **Step 2: Run the tests to confirm they fail**

Run: `uv run --group test pytest tests/research/test_save_as_set.py -q --no-cov`
Expected: `ModuleNotFoundError: No module named 'research.projects'`.

- [ ] **Step 3: Implement `research/projects.py`**

```python
"""Project creation helpers for the search entry point."""

from __future__ import annotations

from .models import Project


def create_project(user, name: str, description: str = "") -> Project:
    cleaned = (name or "").strip()
    if not cleaned:
        raise ValueError("A project needs a name")
    return Project.objects.create(
        owner=user, name=cleaned, description=(description or "").strip()
    )


def project_list_for_user(user):
    return Project.objects.filter(owner=user, is_archived=False).order_by("-modified")
```

- [ ] **Step 4: Add the views**

In `research/views.py`, add:

```python
def _spec_from_search(params) -> QuerySpec:
    document_type = (params.get("document_type") or "all").strip()
    if document_type not in {"agenda", "minutes"}:
        document_type = "all"
    return QuerySpec(
        text=(params.get("query") or "").strip(),
        meeting_name=(params.get("meeting_name_query") or "").strip(),
        municipality_ids=_uuid_values(params.get("municipalities")),
        states=_split_csv(params.get("states")),
        date_from=_date_value(params.get("date_from")),
        date_to=_date_value(params.get("date_to")),
        document_type=document_type,
    )


class ProjectCreateView(LoginRequiredMixin, View):
    def post(self, request):
        try:
            project = create_project(
                request.user,
                request.POST.get("name", ""),
                request.POST.get("description", ""),
            )
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))
        return redirect("research:project-detail", project=project.id)


class SaveAsSetView(LoginRequiredMixin, View):
    template_name = "research/partials/save_as_set_panel.html"

    def get(self, request):
        spec = _spec_from_search(request.GET)
        backend = search_backends.get_search_backend()
        count = backend.count_matches(spec, settings.RESEARCH_SET_MAX_ITEMS + 1)
        over_ceiling = count > settings.RESEARCH_SET_MAX_ITEMS
        hidden_params = [
            {"name": key, "value": value}
            for key, values in request.GET.lists()
            for value in values
        ]
        return render(
            request,
            self.template_name,
            {
                "spec": spec,
                "count": count,
                "over_ceiling": over_ceiling,
                "projects": project_list_for_user(request.user),
                "max_items": settings.RESEARCH_SET_MAX_ITEMS,
                "hidden_params": hidden_params,
            },
        )

    def post(self, request):
        spec = _spec_from_search(request.POST)
        project_id = request.POST.get("project")
        new_project = (request.POST.get("new_project") or "").strip()
        name = (request.POST.get("name") or "").strip() or "Untitled set"

        if new_project:
            try:
                project = create_project(request.user, new_project)
            except ValueError as exc:
                return HttpResponseBadRequest(str(exc))
        else:
            project = get_object_or_404(Project, id=project_id, owner=request.user)

        result_set = create_result_set(project, name, spec)
        return redirect("research:set-detail", project=project.id, set=result_set.id)
```

Import `search_backends`, `settings`, `QuerySpec`, `create_result_set`, `create_project`, `project_list_for_user`, `redirect` as needed.

Add to `research/urls.py`:

```python
path("projects/new/", views.ProjectCreateView.as_view(), name="project-create"),
path("save-as-set/", views.SaveAsSetView.as_view(), name="save-as-set"),
```

Create `templates/research/partials/save_as_set_panel.html`:

```html
<div id="save-as-set-panel" class="fixed inset-0 z-50 flex items-center justify-center bg-gray-500 bg-opacity-75">
    <div class="w-full max-w-lg rounded-lg bg-white p-6 shadow-xl">
        <h2 class="text-lg font-semibold text-gray-900">Save as set</h2>
        <p class="mt-2 text-sm text-gray-600">
            This search matches {{ count }}{% if count > max_items %}+{% endif %} page{{ count|pluralize }}.
        </p>
        {% if over_ceiling %}
            <p class="mt-2 text-sm text-amber-700">
                That is more than the {{ max_items }} page limit. Narrow the search by state, body, or date.
            </p>
        {% else %}
            <form method="post" action="{% url 'research:save-as-set' %}" class="mt-4 space-y-3">
                {% csrf_token %}
                {% for param in hidden_params %}
                    <input type="hidden" name="{{ param.name }}" value="{{ param.value }}">
                {% endfor %}
                <div>
                    <label for="save-as-set-project" class="block text-sm font-medium text-gray-700">Project</label>
                    <select name="project" id="save-as-set-project" class="block w-full rounded-md border-gray-300 shadow-sm text-sm">
                        {% for project in projects %}
                            <option value="{{ project.id }}">{{ project.name }}</option>
                        {% empty %}
                            <option value="">No projects yet</option>
                        {% endfor %}
                    </select>
                </div>
                <div>
                    <label for="save-as-set-new-project" class="block text-sm font-medium text-gray-700">Or create a project</label>
                    <input type="text" name="new_project" id="save-as-set-new-project" class="block w-full rounded-md border-gray-300 shadow-sm text-sm" placeholder="New project name">
                </div>
                <div>
                    <label for="save-as-set-name" class="block text-sm font-medium text-gray-700">Set name</label>
                    <input type="text" name="name" id="save-as-set-name" required class="block w-full rounded-md border-gray-300 shadow-sm text-sm">
                </div>
                <button type="submit" class="inline-flex items-center px-4 py-2 border border-transparent text-sm font-medium rounded-md text-white bg-indigo-600 hover:bg-indigo-700">Create set</button>
            </form>
        {% endif %}
        <a href="{% url 'meetings:meeting-search' %}" class="mt-4 inline-block text-sm text-gray-500 hover:text-gray-700">Cancel</a>
    </div>
</div>
```

The GET view builds `hidden_params` as `[{"name": key, "value": value} for key, values in request.GET.lists() for value in values]`; the form repeats each as a hidden input so the query survives the POST.

Modify `templates/meetings/meeting_search.html` to add the "Save as set" button next to "Save This Search" and a modal container:

```html
<button type="button"
        hx-get="{% url 'research:save-as-set' %}"
        hx-include="#search-form"
        hx-target="#save-as-set-modal"
        hx-swap="innerHTML"
        class="mt-2 w-full inline-flex justify-center items-center px-4 py-2 border border-gray-300 text-sm font-medium rounded-md text-gray-700 bg-white hover:bg-gray-50">
    Save as set
</button>
...
<div id="save-as-set-modal"></div>
```

The button appears only for authenticated users. `hx-include="#search-form"` serializes the form's current filters into the GET request, so the count reflects what the user is about to submit even after HTMX result swaps (the browser URL is not updated by the results HTMX call).

- [ ] **Step 5: Run the tests and commit**

Run: `uv run --group test pytest tests/research/test_save_as_set.py -q --no-cov`
Expected: PASS.

```bash
git add research/projects.py research/views.py research/urls.py templates/research/partials/save_as_set_panel.html templates/meetings/meeting_search.html tests/research/test_save_as_set.py
git commit -m "feat(research): save a search as a research set"
```

---

### Task 6: Clip integration

**Files:**
- Modify: `clip/views.py`
- Modify: `templates/clip/partials/page_preview.html`
- Modify: `templates/clip/clip.html`
- Test: `tests/clip/test_views.py`

**Interfaces:**
- Consumes: `default_set_for_user`, `add_page_to_set`, `Project`, `ResultSet`.
- Produces: `clip:save-page` adds to a research set; the preview lists projects/sets and accepts a note/tags.

- [ ] **Step 1: Update the failing clip tests**

In `tests/clip/test_views.py`, change the save-page tests to assert `SetItem` instead of `NotebookEntry`:

```python
def test_save_page_adds_to_research_set(self, client):
    ...
    response = client.post(
        reverse("clip:save-page"), {"page_id": page.id, "set": str(result_set.id)}
    )
    assert SetItem.objects.filter(result_set=result_set, page=page).exists()
```

Replace the notebook/entry assertions. Add a test that the default set is created when the user has none.

- [ ] **Step 2: Run the tests to confirm they fail**

Run: `uv run --group test pytest tests/clip/test_views.py -q --no-cov`
Expected: FAIL — the view still creates `NotebookEntry`.

- [ ] **Step 3: Update `clip/views.py`**

Replace the notebook imports with:

```python
from research.add_to_set import add_page_to_set, default_set_for_user
from research.models import Project, ResultSet
```

In `FetchPageView._render_preview`, replace the notebooks/tags context with projects and sets:

```python
projects = Project.objects.filter(
    owner=request.user, is_archived=False
).prefetch_related("sets")
```

In `SavePageView.post`, replace the notebook logic with:

```python
page = get_object_or_404(MeetingPage, id=page_id)
result_set = (
    get_object_or_404(ResultSet, id=notebook_id, project__owner=request.user)
    if notebook_id
    else default_set_for_user(request.user)
)
add_page_to_set(
    result_set.project,
    result_set,
    page,
    actor=request.user,
    note=note,
    code_names=request.POST.getlist("codes"),
)
return redirect("research:set-detail", project=result_set.project_id, set=result_set.id)
```

- [ ] **Step 4: Update the clip templates**

In `templates/clip/partials/page_preview.html`, replace the notebook select with a project/set select:

```html
<select name="set" id="set_id" ...>
    {% for project in projects %}
        {% for result_set in project.sets.all %}
            <option value="{{ result_set.id }}">{{ project.name }} / {{ result_set.name }}</option>
        {% endfor %}
    {% empty %}
        <option value="">Create a set first</option>
    {% endfor %}
</select>
```

Rename the submit label to "Add to set". Update `templates/clip/clip.html` title/heading to "Add to Research Set".

- [ ] **Step 5: Run the tests and commit**

Run: `uv run --group test pytest tests/clip -q --no-cov`
Expected: PASS.

```bash
git add clip/views.py templates/clip/partials/page_preview.html templates/clip/clip.html tests/clip/test_views.py
git commit -m "feat(clip): clip pages into research sets"
```

---

### Task 7: Navigation and homepage

**Files:**
- Modify: `templates/base.html`
- Modify: `templates/homepage.html`
- Test: `tests/research/test_navigation.py`

**Interfaces:**
- Consumes: `research:project-list`.
- Produces: no "Notebooks" nav link; "Research" nav link remains; homepage feature blurb links to `/research/`.

- [ ] **Step 1: Write the failing navigation tests**

Create `tests/research/test_navigation.py`:

```python
import pytest
from django.urls import reverse

from tests.factories import UserFactory


@pytest.mark.django_db
class TestNavigation:
    def test_base_navigation_has_research_and_no_notebooks(self, client):
        user = UserFactory()
        client.force_login(user)

        response = client.get(reverse("homepage"))

        content = response.content.decode()
        assert reverse("research:project-list") in content
        assert "Notebooks" not in content

    def test_homepage_links_to_research(self, client):
        response = client.get(reverse("homepage"))
        content = response.content.decode()
        assert reverse("research:project-list") in content
        assert "Notebooks" not in content
```

- [ ] **Step 2: Run the tests to confirm they fail**

Run: `uv run --group test pytest tests/research/test_navigation.py -q --no-cov`
Expected: FAIL — "Notebooks" still appears.

- [ ] **Step 3: Update the templates**

In `templates/base.html`, remove both Notebooks links (desktop and mobile) but keep the Research links. Update the `data-umami-event-destination` values to `research`.

In `templates/homepage.html`, update the "Feature 2: Notebooks" block:
- Heading "Notebooks" → "Research Projects".
- "My Research Notebook" → "My Research Project".
- "Clip important pages ... to your notebooks" → "Search the whole corpus, freeze the matches into a set, and code what matters."
- Link "Create your first notebook" → "Start a research project" pointing at `{% url 'research:project-list' %}`.
- Update the demo's `x-data="notebookDemo()"` name only if the JS function is renamed; keep the demo but update copy.

- [ ] **Step 4: Run the tests and commit**

Run: `uv run --group test pytest tests/research/test_navigation.py -q --no-cov`
Expected: PASS.

```bash
git add templates/base.html templates/homepage.html tests/research/test_navigation.py
git commit -m "feat: point navigation and homepage at research projects"
```

---

### Task 8: Final review and fix pass

**Files:**
- Modify: any files from Tasks 1-7 as the review requires.
- Test: the affected tests.

- [ ] **Step 1: Run the whole research, notebook, clip and navigation suites**

Run: `uv run --group test pytest tests/research tests/notebooks tests/clip -q --no-cov`
Expected: PASS.

- [ ] **Step 2: Run type and lint checks**

Run:
```bash
uv run mypy .
uv run --group dev ruff check .
uv run --group dev ruff format --check .
```
Expected: clean.

- [ ] **Step 3: Run the migration forward/backward/forward smoke test**

Run:
```bash
uv run python manage.py migrate research 0004 --noinput
uv run python manage.py migrate research 0003 --noinput
uv run python manage.py migrate research 0004 --noinput
uv run python manage.py verify_notebook_migration
```
Expected: no errors; verification reports OK for migrated notebooks.

- [ ] **Step 4: Final whole-branch review**

Dispatch a fresh reviewer against `origin/main..HEAD` using `superpowers:requesting-code-review`. Address every Critical/Important finding; record deferred minors in the SDD ledger.

## Final Review Focus

1. The migration must be safe to run on production data: no N+1 per entry beyond the documented prefetch, no writes to the `notebooks` tables, and no event spam on re-run.
2. A notebook whose user has no other projects must still get a valid "My research" project for orphan tags, without creating duplicate "My research" projects.
3. The "Save as set" count must not exceed the set ceiling silently; the panel and the POST must agree on the same `QuerySpec`.
4. The "Add to set" and "Save as set" panels must be CSRF-protected and 404 for another user's project/set.
5. The clip flow must preserve note/tag behavior or explicitly drop it with a test documenting the choice.
6. Redirects must be permanent and must not expose another user's project data.
7. Navigation/homepage must not leave dead `/notebooks/` links.
