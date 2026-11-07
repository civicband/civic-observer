# Research Projects — Phase 1A Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the `research` data model, materialize a frozen set from a query, and give a signed-in user project/set pages with a filterable, keyset-paginated view of the set's items.

**Architecture:** A new `research` app owns eight models. Set materialization runs one `INSERT ... SELECT` through `searches.queryspec.build_where` in production, with an ORM fallback used only by tests. A `Lens` value object encodes filters in the URL; `research.browse` applies it to `research_setitem` so browsing never scans the page corpus. Views are server-rendered + htmx, scoped to `project__owner=request.user`.

**Tech Stack:** Django 5.2, PostgreSQL 17, ParadeDB pg_search, django-rq, htmx, Alpine, Tailwind, pytest, factory_boy.

**Spec:** `docs/superpowers/specs/2026-11-06-research-projects-design.md`

## Global Constraints

- **Vocabulary:** models/API/exports use `Code`, `Coding`, "codebook"; the UI uses "Tags", "Tag guide". Do not rename either side.
- **Page references:** `db_constraint=False, on_delete=DO_NOTHING` for every page FK.
- **Sets are snapshots.** No live membership; new matches arrive only through an explicit logged action (Phase 1C).
- **Owner scoping:** every view is `LoginRequiredMixin` and filters through `project__owner=request.user`; other users' objects are 404.
- **One query layer:** corpus predicates use `QuerySpec` + `build_where`; set-local predicates use `research_setitem` only.
- **No silent truncation:** a set over `RESEARCH_SET_MAX_ITEMS` becomes `TOO_LARGE` with the count, never a partial set.
- **Queue:** materialization runs on the `research` queue unless the set is at/under `RESEARCH_INLINE_SET_MAX_ITEMS`.
- **Choice values are lowercase strings** (`"query"`, `"lens"`, `"manual"`, `"import"`, `"pending"`, `"materializing"`, `"ready"`, `"failed"`, `"too_large"`) so raw SQL, JSON and the URL agree.
- **Gate A still open:** `RESEARCH_SET_MAX_ITEMS` stays the provisional 500,000 until `research_benchmark` runs on production-sized data. Do not treat it as final.

## Review Focus

1. Deleting a `MeetingPage` must leave `SetItem`/`Coding` rows intact (`db_constraint=False` + `DO_NOTHING`), and the UI must tolerate a missing page (Task 1).
2. Re-running materialization must never duplicate items (`ON CONFLICT`/`ignore_conflicts`) (Task 2).
3. Over-ceiling sets must end `TOO_LARGE` with a count and zero items, never a partial set (Task 2).
4. Keyset pagination must not duplicate or skip rows across pages, including `rank IS NULL` (Task 3).
5. Every view must 404 for another user's project/set, and status/items partials must be owner-scoped too (Task 4).

---

### Task 1: `research` app and models

**Files:**
- Create: `research/__init__.py`, `research/apps.py`, `research/models.py`, `research/admin.py`
- Create: `research/migrations/0001_initial.py` (via `makemigrations`)
- Modify: `config/settings/base.py` (`LOCAL_APPS`)
- Modify: `pyproject.toml` (`--cov=research`)
- Modify: `tests/factories.py` (research factories)
- Test: `tests/research/__init__.py`, `tests/research/test_models.py`

**Interfaces:**
- Produces models: `Project`, `ResultSet` (`Origin`, `Status`), `SetItem` (`Disposition`, `AddedVia`), `Code`, `Coding`, `Memo`, `IgnoreRule`, `AuditEvent`.
- Produces factories: `ProjectFactory`, `ResultSetFactory`, `SetItemFactory`, `CodeFactory`, `CodingFactory`, `MemoFactory`, `IgnoreRuleFactory`, `AuditEventFactory`.

- [ ] **Step 1: Write the failing model tests**

Create `tests/research/__init__.py` (empty) and `tests/research/test_models.py`:

```python
import uuid

import pytest
from django.db import IntegrityError, transaction

from meetings.models import MeetingPage
from research.models import (
    AuditEvent,
    Code,
    Coding,
    IgnoreRule,
    Memo,
    Project,
    ResultSet,
    SetItem,
)
from tests.factories import (
    CodeFactory,
    CodingFactory,
    MeetingPageFactory,
    ProjectFactory,
    ResultSetFactory,
    SetItemFactory,
    UserFactory,
)


@pytest.mark.django_db
class TestProject:
    def test_owner_scoped(self):
        user = UserFactory()
        project = ProjectFactory(owner=user)
        assert project.owner == user
        assert list(user.research_projects.all()) == [project]

    def test_defaults(self):
        project = ProjectFactory()
        assert project.description == ""
        assert project.is_archived is False


@pytest.mark.django_db
class TestResultSet:
    def test_defaults(self):
        result_set = ResultSetFactory()
        assert result_set.status == ResultSet.Status.PENDING
        assert result_set.origin == ResultSet.Origin.QUERY
        assert result_set.item_count == 0
        assert result_set.materialized_at is None
        assert result_set.parent is None

    def test_child_set_links_parent(self):
        parent = ResultSetFactory()
        child = ResultSetFactory(parent=parent, origin=ResultSet.Origin.LENS)
        assert child.parent == parent
        assert list(parent.children.all()) == [child]


@pytest.mark.django_db
class TestSetItem:
    def test_unique_page_per_set(self):
        item = SetItemFactory()
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                SetItemFactory(result_set=item.result_set, page=item.page)

    def test_same_page_in_two_sets(self):
        first = SetItemFactory()
        second = SetItemFactory(page=first.page)
        assert second.page == first.page

    def test_page_deletion_keeps_item(self):
        item = SetItemFactory()
        page_id = item.page_id
        MeetingPage.objects.filter(pk=page_id).delete()
        item.refresh_from_db()
        assert item.page_id == page_id


@pytest.mark.django_db
class TestCode:
    def test_root_code_name_unique_per_project(self):
        existing = CodeFactory()
        assert existing.parent is None
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                CodeFactory(project=existing.project, name=existing.name)

    def test_same_name_under_different_parents(self):
        project = ProjectFactory()
        parent_a = CodeFactory(project=project, name="Housing")
        parent_b = CodeFactory(project=project, name="Budget")
        child_a = CodeFactory(project=project, parent=parent_a, name="Zoning")
        child_b = CodeFactory(project=project, parent=parent_b, name="Zoning")
        assert child_a.name == child_b.name


@pytest.mark.django_db
class TestCoding:
    def test_one_whole_page_coding_per_code_and_page(self):
        coding = CodingFactory()
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                CodingFactory(
                    project=coding.project, code=coding.code, page=coding.page
                )

    def test_span_must_be_ordered(self):
        coding = CodingFactory()
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                CodingFactory(
                    project=coding.project,
                    code=coding.code,
                    page=coding.page,
                    span_start=10,
                    span_end=10,
                )

    def test_defaults(self):
        coding = CodingFactory()
        assert coding.span_start is None
        assert coding.span_end is None
        assert coding.is_exemplar is False
        assert coding.page_hash == ""


@pytest.mark.django_db
class TestMemoAndAudit:
    def test_project_memo_has_no_target(self):
        memo = Memo.objects.create(project=ProjectFactory(), body="Journal")
        assert memo.result_set is None
        assert memo.code is None
        assert memo.page is None

    def test_event_is_appended(self):
        project = ProjectFactory()
        event = AuditEvent.objects.create(project=project, verb="set.materialized")
        assert event.created is not None
        assert list(project.events.all()) == [event]


@pytest.mark.django_db
class TestIgnoreRule:
    def test_defaults(self):
        rule = IgnoreRule.objects.create(
            project=ProjectFactory(),
            name="Boilerplate",
            predicate={"text": "roll call"},
        )
        assert rule.is_active is True
        assert rule.reason == ""
```

Add the factories to `tests/factories.py` (import `research.models` at the top):

```python
class ProjectFactory(DjangoModelFactory):
    class Meta:
        model = research.models.Project

    owner = factory.SubFactory(UserFactory)  # type: ignore
    name = factory.Faker("sentence", nb_words=3)  # type: ignore
    description = ""


class ResultSetFactory(DjangoModelFactory):
    class Meta:
        model = research.models.ResultSet

    project = factory.SubFactory(ProjectFactory)  # type: ignore
    name = factory.Faker("sentence", nb_words=3)  # type: ignore
    origin = "query"
    query = factory.LazyFunction(dict)  # type: ignore
    query_hash = ""


class SetItemFactory(DjangoModelFactory):
    class Meta:
        model = research.models.SetItem

    result_set = factory.SubFactory(ResultSetFactory)  # type: ignore
    page = factory.SubFactory(MeetingPageFactory)  # type: ignore
    rank = 1.0
    meeting_date = factory.LazyAttribute(lambda o: o.page.meeting_date)  # type: ignore
    municipality_id = factory.LazyAttribute(lambda o: o.page.municipality_id)  # type: ignore
    state = factory.LazyAttribute(lambda o: o.page.state)  # type: ignore
    document_type = factory.LazyAttribute(lambda o: o.page.document_type)  # type: ignore
    added_via = "query"


class CodeFactory(DjangoModelFactory):
    class Meta:
        model = research.models.Code

    project = factory.SubFactory(ProjectFactory)  # type: ignore
    name = factory.Sequence(lambda n: f"Code {n}")  # type: ignore


class CodingFactory(DjangoModelFactory):
    class Meta:
        model = research.models.Coding

    project = factory.LazyAttribute(lambda o: o.code.project)  # type: ignore
    code = factory.SubFactory(CodeFactory)  # type: ignore
    page = factory.SubFactory(MeetingPageFactory)  # type: ignore


class MemoFactory(DjangoModelFactory):
    class Meta:
        model = research.models.Memo

    project = factory.SubFactory(ProjectFactory)  # type: ignore
    body = factory.Faker("paragraph")  # type: ignore


class IgnoreRuleFactory(DjangoModelFactory):
    class Meta:
        model = research.models.IgnoreRule

    project = factory.SubFactory(ProjectFactory)  # type: ignore
    name = factory.Faker("sentence", nb_words=3)  # type: ignore
    predicate = factory.LazyFunction(dict)  # type: ignore


class AuditEventFactory(DjangoModelFactory):
    class Meta:
        model = research.models.AuditEvent

    project = factory.SubFactory(ProjectFactory)  # type: ignore
    verb = "set.materialized"
    payload = factory.LazyFunction(dict)  # type: ignore
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --group test pytest tests/research/test_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'research'`.

- [ ] **Step 3: Create the app and models**

Create `research/__init__.py` (empty), `research/apps.py`:

```python
from django.apps import AppConfig


class ResearchConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "research"
```

Create `research/models.py`:

```python
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone
from model_utils.models import TimeStampedModel


class Project(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="research_projects",
    )
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    is_archived = models.BooleanField(default=False)

    class Meta:
        ordering = ["-modified"]

    def __str__(self) -> str:
        return self.name


class ResultSet(TimeStampedModel):
    class Origin(models.TextChoices):
        QUERY = "query", "Query"
        LENS = "lens", "Lens"
        MANUAL = "manual", "Manual"
        IMPORT = "import", "Import"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        MATERIALIZING = "materializing", "Materializing"
        READY = "ready", "Ready"
        FAILED = "failed", "Failed"
        TOO_LARGE = "too_large", "Too large"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="sets")
    parent = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, related_name="children"
    )
    name = models.CharField(max_length=200)
    origin = models.CharField(max_length=16, choices=Origin, default=Origin.QUERY)
    query = models.JSONField(default=dict)
    query_hash = models.CharField(max_length=64, blank=True, default="")
    status = models.CharField(max_length=16, choices=Status, default=Status.PENDING)
    item_count = models.IntegerField(default=0)
    materialized_at = models.DateTimeField(null=True, blank=True)
    last_checked_at = models.DateTimeField(null=True, blank=True)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ["-created"]

    def __str__(self) -> str:
        return self.name


class Code(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="codes")
    parent = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="children"
    )
    name = models.CharField(max_length=100)
    color = models.CharField(max_length=7, blank=True)
    definition = models.TextField(blank=True)
    inclusion = models.TextField(blank=True)
    exclusion = models.TextField(blank=True)
    is_archived = models.BooleanField(default=False)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "parent", "name"],
                nulls_distinct=False,
                name="research_code_uniq_name",
            )
        ]

    def __str__(self) -> str:
        return self.name


class IgnoreRule(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="ignore_rules"
    )
    name = models.CharField(max_length=200)
    predicate = models.JSONField(default=dict)
    reason = models.CharField(max_length=500, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-created"]

    def __str__(self) -> str:
        return self.name


class AuditEvent(models.Model):
    id = models.BigAutoField(primary_key=True)
    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="events"
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    verb = models.CharField(max_length=50)
    result_set = models.ForeignKey(
        ResultSet, on_delete=models.SET_NULL, null=True, blank=True
    )
    payload = models.JSONField(default=dict)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created"]
        indexes = [
            models.Index(
                fields=["project", "-created"], name="research_event_proj_created_idx"
            )
        ]

    def __str__(self) -> str:
        return self.verb


class SetItem(models.Model):
    class Disposition(models.TextChoices):
        UNREVIEWED = "u", "Unreviewed"
        RELEVANT = "r", "Relevant"
        IGNORED = "i", "Ignored"

    class AddedVia(models.TextChoices):
        QUERY = "query", "Query"
        LENS = "lens", "Lens"
        NEW_MATCHES = "new_matches", "New matches"
        MANUAL = "manual", "Manual"
        IMPORT = "import", "Import"

    id = models.BigAutoField(primary_key=True)
    result_set = models.ForeignKey(
        ResultSet, on_delete=models.CASCADE, related_name="items", db_index=False
    )
    page = models.ForeignKey(
        "meetings.MeetingPage",
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="+",
    )
    rank = models.FloatField(null=True, blank=True)
    meeting_date = models.DateField(null=True, blank=True)
    municipality_id = models.UUIDField(null=True, blank=True)
    state = models.CharField(max_length=64, blank=True)
    document_type = models.CharField(max_length=32, blank=True)
    disposition = models.CharField(
        max_length=1, choices=Disposition, default=Disposition.UNREVIEWED
    )
    previous_disposition = models.CharField(max_length=1, blank=True, default="")
    disposition_by_rule = models.ForeignKey(
        IgnoreRule, on_delete=models.SET_NULL, null=True, blank=True
    )
    ignore_reason = models.CharField(max_length=500, blank=True)
    last_event = models.ForeignKey(
        AuditEvent,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        db_constraint=False,
    )
    added_via = models.CharField(
        max_length=16, choices=AddedVia, default=AddedVia.QUERY
    )
    added_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-rank", "page"]
        constraints = [
            models.UniqueConstraint(
                fields=["result_set", "page"], name="research_setitem_uniq_item"
            )
        ]
        indexes = [
            models.Index(
                "result_set",
                "disposition",
                models.F("rank").desc(),
                "page",
                name="research_item_set_disp_rank_idx",
            ),
            models.Index(
                fields=["result_set", "meeting_date", "page"],
                name="research_item_set_date_page_idx",
            ),
            models.Index(
                fields=["result_set", "municipality_id"],
                name="research_item_set_muni_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.result_set_id}:{self.page_id}"


class Coding(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="codings"
    )
    code = models.ForeignKey(Code, on_delete=models.CASCADE, related_name="codings")
    page = models.ForeignKey(
        "meetings.MeetingPage",
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="+",
    )
    span_start = models.PositiveIntegerField(null=True, blank=True)
    span_end = models.PositiveIntegerField(null=True, blank=True)
    quote = models.TextField(blank=True)
    page_hash = models.CharField(max_length=64, blank=True)
    is_exemplar = models.BooleanField(default=False)
    coder = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(span_start__isnull=True, span_end__isnull=True)
                    | models.Q(span_start__lt=models.F("span_end"))
                ),
                name="research_coding_valid_span",
            ),
            models.UniqueConstraint(
                fields=["code", "page"],
                condition=models.Q(span_start__isnull=True),
                name="research_coding_uniq_page",
            ),
            models.UniqueConstraint(
                fields=["code", "page", "span_start", "span_end"],
                name="research_coding_uniq_span",
            ),
        ]
        indexes = [
            models.Index(
                fields=["project", "page"], name="research_coding_proj_page_idx"
            ),
            models.Index(fields=["code", "page"], name="research_coding_code_page_idx"),
        ]


class Memo(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="memos")
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    body = models.TextField()
    result_set = models.ForeignKey(
        ResultSet, on_delete=models.CASCADE, null=True, blank=True
    )
    code = models.ForeignKey(Code, on_delete=models.CASCADE, null=True, blank=True)
    page = models.ForeignKey(
        "meetings.MeetingPage",
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        ordering = ["-created"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(
                        result_set__isnull=True, code__isnull=True, page__isnull=True
                    )
                    | models.Q(
                        result_set__isnull=False, code__isnull=True, page__isnull=True
                    )
                    | models.Q(
                        result_set__isnull=True, code__isnull=False, page__isnull=True
                    )
                    | models.Q(
                        result_set__isnull=True, code__isnull=True, page__isnull=False
                    )
                ),
                name="research_memo_at_most_one_target",
            )
        ]
```

Create `research/admin.py` registering all models with `raw_id_fields` for user/page FKs:

```python
from django.contrib import admin

from .models import (
    AuditEvent,
    Code,
    Coding,
    IgnoreRule,
    Memo,
    Project,
    ResultSet,
    SetItem,
)


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ["name", "owner", "is_archived", "modified"]
    list_filter = ["is_archived"]
    raw_id_fields = ["owner"]


@admin.register(ResultSet)
class ResultSetAdmin(admin.ModelAdmin):
    list_display = ["name", "project", "origin", "status", "item_count", "modified"]
    list_filter = ["origin", "status"]
    raw_id_fields = ["project", "parent"]


@admin.register(SetItem)
class SetItemAdmin(admin.ModelAdmin):
    list_display = ["result_set", "page", "disposition", "added_via"]
    list_filter = ["disposition", "added_via"]
    raw_id_fields = ["result_set", "page", "last_event", "disposition_by_rule"]


@admin.register(Code)
class CodeAdmin(admin.ModelAdmin):
    list_display = ["name", "project", "parent", "is_archived"]
    raw_id_fields = ["project", "parent"]


@admin.register(Coding)
class CodingAdmin(admin.ModelAdmin):
    list_display = ["code", "page", "project", "is_exemplar"]
    raw_id_fields = ["project", "code", "page", "coder"]


@admin.register(Memo)
class MemoAdmin(admin.ModelAdmin):
    list_display = ["project", "author", "created"]
    raw_id_fields = ["project", "author", "result_set", "code", "page"]


@admin.register(IgnoreRule)
class IgnoreRuleAdmin(admin.ModelAdmin):
    list_display = ["name", "project", "is_active"]
    raw_id_fields = ["project"]


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = ["verb", "project", "result_set", "created"]
    list_filter = ["verb"]
    raw_id_fields = ["project", "actor", "result_set"]
```

Add `"research",` to `LOCAL_APPS` in `config/settings/base.py`, add `"--cov=research",` to `pyproject.toml` addopts, then generate the migration:

```bash
uv run python manage.py makemigrations research
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group test pytest tests/research/test_models.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add research config/settings/base.py pyproject.toml tests/factories.py tests/research
git commit -m "feat: add research data model"
```

---

### Task 2: Set materialization

**Files:**
- Modify: `searches/queryspec.py` (`from_canonical`)
- Modify: `searches/search_backends.py` (`count_matches`)
- Modify: `tests/conftest.py` (mock `count_matches`)
- Modify: `config/settings/base.py` (`RESEARCH_USE_SQL_MATERIALIZATION`), `config/settings/test.py` (False)
- Create: `research/materialize.py`, `research/tasks.py`
- Test: `tests/searches/test_queryspec.py`, `tests/research/test_materialize.py`

**Interfaces:**
- Consumes: `QuerySpec`, `build_where`, `get_search_backend`, `SetItem`, `AuditEvent`, `ResultSet`.
- Produces:
  - `QuerySpec.from_canonical(data: dict) -> QuerySpec`
  - `SearchBackend.count_matches(spec: QuerySpec, cap: int) -> int`
  - `research.materialize.create_result_set(project, name, spec, origin=ResultSet.Origin.QUERY, parent=None) -> ResultSet`
  - `research.materialize.materialize_result_set(result_set_id: UUID) -> ResultSet`
  - `research.materialize.enqueue_materialization(result_set_id: UUID) -> None`
  - `research.tasks.materialize_result_set_task(result_set_id) -> None`

- [ ] **Step 1: Write the failing tests**

Append to `tests/searches/test_queryspec.py`:

```python
class TestFromCanonical:
    def test_round_trips_canonical_json(self):
        spec = QuerySpec(
            text=" housing ",
            meeting_name=" council ",
            municipality_ids=(uuid.UUID(int=3),),
            states=("OR", "CA"),
            date_from=date(2024, 1, 2),
            date_to=date(2024, 3, 4),
            document_type="agenda",
            modified_after=datetime(2024, 6, 1, tzinfo=timezone.utc),
        )
        rebuilt = QuerySpec.from_canonical(spec.canonical())
        assert rebuilt.canonical() == spec.canonical()
        assert rebuilt.query_hash() == spec.query_hash()
```

Create `tests/research/test_materialize.py`:

```python
from datetime import date
from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone

from research.materialize import create_result_set, materialize_result_set
from research.models import AuditEvent, ResultSet, SetItem
from searches.queryspec import QuerySpec
from tests.factories import (
    MeetingDocumentFactory,
    MeetingPageFactory,
    MuniFactory,
    ProjectFactory,
)


@pytest.mark.django_db
class TestCreateResultSet:
    def test_inline_materializes_and_records_audit(self, settings):
        settings.RESEARCH_INLINE_SET_MAX_ITEMS = 1000
        settings.RESEARCH_SET_MAX_ITEMS = 500_000
        muni = MuniFactory()
        doc = MeetingDocumentFactory(municipality=muni, meeting_date=date(2024, 3, 1))
        first = MeetingPageFactory(document=doc, text="housing policy")
        second = MeetingPageFactory(document=doc, text="housing budget")
        MeetingPageFactory(document=doc, text="zoning")
        project = ProjectFactory()

        result_set = create_result_set(
            project, "Housing", QuerySpec(text="housing", municipality_ids=(muni.id,))
        )

        assert result_set.status == ResultSet.Status.READY
        assert result_set.item_count == 2
        assert set(result_set.items.values_list("page_id", flat=True)) == {
            first.id,
            second.id,
        }
        event = AuditEvent.objects.get(project=project, verb="set.materialized")
        assert event.payload["item_count"] == 2
        assert event.payload["query"]["text"] == "housing"

    def test_over_ceiling_becomes_too_large(self, settings):
        settings.RESEARCH_SET_MAX_ITEMS = 1
        settings.RESEARCH_INLINE_SET_MAX_ITEMS = 1
        muni = MuniFactory()
        doc = MeetingDocumentFactory(municipality=muni)
        MeetingPageFactory(document=doc, text="housing")
        MeetingPageFactory(document=doc, text="housing")
        project = ProjectFactory()

        result_set = create_result_set(project, "Too big", QuerySpec(text="housing"))

        assert result_set.status == ResultSet.Status.TOO_LARGE
        assert result_set.item_count == 2
        assert result_set.items.count() == 0
        assert "narrow" in result_set.error.lower()

    def test_rerun_is_idempotent(self, settings):
        settings.RESEARCH_SET_MAX_ITEMS = 500_000
        settings.RESEARCH_INLINE_SET_MAX_ITEMS = 1000
        muni = MuniFactory()
        doc = MeetingDocumentFactory(municipality=muni)
        page = MeetingPageFactory(document=doc, text="housing")
        project = ProjectFactory()
        result_set = create_result_set(project, "Housing", QuerySpec(text="housing"))

        materialize_result_set(result_set.id)

        assert result_set.items.count() == 1
        assert result_set.items.get().page_id == page.id


@pytest.mark.django_db
class TestSqlMaterialization:
    @patch("research.materialize.connection")
    @patch("research.materialize.settings")
    def test_sql_uses_build_where_and_conflict_clause(self, mock_settings, mock_conn):
        mock_settings.RESEARCH_USE_SQL_MATERIALIZATION = True
        mock_settings.RESEARCH_SET_MAX_ITEMS = 500_000
        mock_settings.RESEARCH_INLINE_SET_MAX_ITEMS = 1000
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_cursor.rowcount = 3

        project = ProjectFactory()
        result_set = ResultSet.objects.create(
            project=project,
            name="Housing",
            origin=ResultSet.Origin.QUERY,
            query=QuerySpec(text="housing").canonical(),
            query_hash=QuerySpec(text="housing").query_hash(),
        )

        materialize_result_set(result_set.id)

        sql = mock_cursor.execute.call_args[0][0]
        assert "INSERT INTO research_setitem" in sql
        assert "paradedb.score(mp.id)" in sql
        assert "ON CONFLICT (result_set_id, page_id) DO NOTHING" in sql
        assert "mp.id @@@ paradedb.parse(%s)" in sql
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --group test pytest tests/searches/test_queryspec.py tests/research/test_materialize.py -v`
Expected: FAIL (`from_canonical` missing; `research.materialize` missing).

- [ ] **Step 3: Add `from_canonical`, `count_matches`, settings**

In `searches/queryspec.py`:

```python
@classmethod
def from_canonical(cls, data: dict) -> "QuerySpec":
    return cls(
        text=data.get("text", ""),
        meeting_name=data.get("meeting_name", ""),
        municipality_ids=tuple(
            uuid.UUID(value) for value in data.get("municipality_ids", [])
        ),
        states=tuple(data.get("states", [])),
        date_from=(
            date.fromisoformat(data["date_from"]) if data.get("date_from") else None
        ),
        date_to=date.fromisoformat(data["date_to"]) if data.get("date_to") else None,
        document_type=data.get("document_type", "all"),
        modified_after=(
            datetime.fromisoformat(data["modified_after"])
            if data.get("modified_after")
            else None
        ),
    )
```

In `searches/search_backends.py`, add to the abstract class and `PgSearchBackend`:

```python
def count_matches(self, spec: QuerySpec, cap: int) -> int:
    """Return the number of matches, counting no more than ``cap``."""
    where_sql, params = build_where(spec)
    sql = f"""
        SELECT count(*) FROM (
            SELECT 1 FROM meetings_meetingpage mp
            WHERE {where_sql}
            LIMIT %s
        ) capped
    """
    with connection.cursor() as cur:
        cur.execute(sql, params + [cap])
        return cur.fetchone()[0]
```

In `tests/conftest.py`, add a `count_matches` side effect that reuses the ORM filters:

```python
def count_side_effect(spec, cap):
    qs = MeetingPage.objects.all()
    if spec.text:
        qs = qs.filter(text__icontains=spec.text)
    if spec.meeting_name:
        qs = qs.filter(meeting_name__icontains=spec.meeting_name)
    if spec.municipality_ids:
        qs = qs.filter(municipality_id__in=spec.municipality_ids)
    if spec.states:
        qs = qs.filter(state__in=spec.states)
    if spec.date_from:
        qs = qs.filter(meeting_date__gte=spec.date_from)
    if spec.date_to:
        qs = qs.filter(meeting_date__lte=spec.date_to)
    if spec.document_type and spec.document_type != "all":
        qs = qs.filter(document_type=spec.document_type)
    if spec.modified_after:
        qs = qs.filter(modified__gte=spec.modified_after)
    return min(qs.count(), cap)


mock_backend.count_matches.side_effect = count_side_effect
```

In `config/settings/base.py`:

```python
RESEARCH_USE_SQL_MATERIALIZATION = env.bool("RESEARCH_USE_SQL_MATERIALIZATION", True)
```

In `config/settings/test.py`:

```python
RESEARCH_USE_SQL_MATERIALIZATION = False
```

- [ ] **Step 4: Implement the materialization service**

Create `research/tasks.py`:

```python
from .materialize import materialize_result_set


def materialize_result_set_task(result_set_id) -> None:
    materialize_result_set(result_set_id)
```

Create `research/materialize.py` with `create_result_set`, `enqueue_materialization`, `materialize_result_set`, `_insert_items_sql`, `_insert_items_orm`, and a `_spec_from_set` helper that rebuilds `QuerySpec` from `result_set.query`.

Key behavior:
- `create_result_set` creates the row, calls `backend.count_matches(spec, settings.RESEARCH_SET_MAX_ITEMS + 1)`, sets `TOO_LARGE` and an error string containing "narrow" when over, otherwise runs inline if `count <= settings.RESEARCH_INLINE_SET_MAX_ITEMS` else enqueues on `settings.RESEARCH_QUEUE` with `job_timeout=settings.RESEARCH_JOB_TIMEOUT`.
- `materialize_result_set` wraps the insert and final updates in `transaction.atomic()`, sets `MATERIALIZING`, inserts, recomputes `item_count` from `SetItem`, sets `materialized_at=timezone.now()`, sets `READY`, writes `AuditEvent(verb="set.materialized", payload={"query": result_set.query, "item_count": ..., "duration_ms": ..., "ignored_by_rule": {}})`.
- On exception set `FAILED` with the error text and re-raise.
- SQL insert: `paradedb.score(mp.id)` for rank, `added_via` parameter, `ON CONFLICT (result_set_id, page_id) DO NOTHING`, `result_set.materialized_at` from `now()` in Python.
- ORM insert: page through `backend.search(...)`, hydrate pages, `bulk_create(ignore_conflicts=True)`, `rank=None`, copy `meeting_date/municipality_id/state/document_type` and `added_via=result_set.origin`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run --group test pytest tests/searches/test_queryspec.py tests/research/test_materialize.py tests/searches -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add research/materialize.py research/tasks.py searches/queryspec.py searches/search_backends.py tests/conftest.py config/settings tests/searches/test_queryspec.py tests/research/test_materialize.py
git commit -m "feat: materialize research result sets"
```

---

### Task 3: Lens and set-local browsing

**Files:**
- Create: `research/lens.py`, `research/browse.py`
- Test: `tests/research/test_lens.py`, `tests/research/test_browse.py`

**Interfaces:**
- Produces:
  - `Lens(text="", dispositions=("u","r"), codes_all=(), codes_any=(), codes_none=(), uncoded=False, municipality_ids=(), states=(), date_from=None, date_to=None, document_type="all", added_via=(), order="rank")`
  - `Lens.from_querydict(qd) -> Lens`, `Lens.to_querystring() -> str`
  - `research.browse.browse_set(result_set, lens, *, limit=50, after=None) -> tuple[list[SetItem], str | None]`
  - `research.browse.set_facets(result_set, lens) -> dict`

- [ ] **Step 1: Write the failing tests**

Create `tests/research/test_lens.py` covering query-string parsing/round-trip and defaults.

Create `tests/research/test_browse.py`:

```python
import pytest

from research.browse import browse_set, set_facets
from research.lens import Lens
from research.models import Coding, SetItem
from tests.factories import (
    CodingFactory,
    MeetingPageFactory,
    ResultSetFactory,
    SetItemFactory,
)


@pytest.mark.django_db
class TestBrowseSet:
    def _items(self):
        result_set = ResultSetFactory()
        return result_set, [
            SetItemFactory(
                result_set=result_set,
                rank=3.0,
                page=MeetingPageFactory(text="housing"),
            ),
            SetItemFactory(
                result_set=result_set,
                rank=2.0,
                page=MeetingPageFactory(text="housing"),
            ),
            SetItemFactory(
                result_set=result_set,
                rank=1.0,
                page=MeetingPageFactory(text="housing"),
            ),
        ]

    def test_ignored_hidden_by_default(self):
        result_set, items = self._items()
        items[1].disposition = SetItem.Disposition.IGNORED
        items[1].save()
        visible, _ = browse_set(result_set, Lens())
        assert items[1] not in visible
        assert len(visible) == 2

    def test_keyset_pagination_does_not_repeat(self):
        result_set, items = self._items()
        first_page, after = browse_set(result_set, Lens(), limit=2)
        second_page, after2 = browse_set(result_set, Lens(), limit=2, after=after)
        assert first_page + second_page == items
        assert after2 is None

    def test_rank_null_sorts_last_and_paginates(self):
        result_set = ResultSetFactory()
        ranked = SetItemFactory(result_set=result_set, rank=1.0)
        unranked_a = SetItemFactory(result_set=result_set, rank=None)
        unranked_b = SetItemFactory(result_set=result_set, rank=None)
        first_page, after = browse_set(result_set, Lens(), limit=2)
        second_page, _ = browse_set(result_set, Lens(), limit=2, after=after)
        assert first_page == [ranked, unranked_a]
        assert second_page == [unranked_b]

    def test_state_and_disposition_filters(self):
        result_set, items = self._items()
        items[0].state = "CA"
        items[0].disposition = SetItem.Disposition.RELEVANT
        items[0].save()
        visible, _ = browse_set(result_set, Lens(states=("CA",), dispositions=("r",)))
        assert visible == [items[0]]

    def test_code_filters(self):
        result_set, items = self._items()
        code = CodingFactory(project=result_set.project, page=items[0].page).code
        assert browse_set(result_set, Lens(codes_all=(code.id,)))[0] == [items[0]]
        assert browse_set(result_set, Lens(codes_none=(code.id,)))[0] == items[1:]
        assert browse_set(result_set, Lens(uncoded=True))[0] == items[1:]

    def test_text_subsearch_uses_backend(self):
        result_set, items = self._items()
        visible, _ = browse_set(result_set, Lens(text="housing"))
        assert visible == items


@pytest.mark.django_db
class TestFacets:
    def test_counts_respect_the_lens(self):
        result_set = ResultSetFactory()
        first = SetItemFactory(
            result_set=result_set, state="CA", document_type="agenda"
        )
        second = SetItemFactory(
            result_set=result_set, state="OR", document_type="minutes"
        )
        facets = set_facets(result_set, Lens(states=("CA",)))
        assert facets["states"] == [{"state": "CA", "count": 1}]
        assert {row["document_type"] for row in facets["document_types"]} == {"agenda"}
        assert first.state == "CA" and second.state == "OR"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --group test pytest tests/research/test_lens.py tests/research/test_browse.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `Lens` and `browse`**

`research/lens.py`:
- Parse comma-separated list params (`disposition`, `state`, `municipality`, `code_any`, `code_all`, `code_none`, `added_via`) and scalar params (`q`, `date_from`, `date_to`, `document_type`, `order`).
- `to_querystring()` emits only non-default values, in a stable order.

`research/browse.py`:
- `_filtered_items(result_set, lens)` starts from `SetItem.objects.filter(result_set=result_set)` and applies dispositions, facets, code `Exists`/`~Exists`, and text.
- Text: call `get_search_backend().search(...)` with a `QuerySpec` built from the set spec (or bare text for non-query sets), page through ids, then `page_id__in=ids`. This is the Phase 1A path; the SQL semi-join optimization is deferred.
- Ordering: `rank` → `F("rank").desc(nulls_last=True), "page"`; `date_desc`/`date_asc` on `meeting_date` then `page`.
- Keyset: encode `f"{rank or ''}|{page_id}"`; apply `(rank < value) | (rank=value & page > page_id)`, with a NULL branch for rankless tails.
- `browse_set` fetches `limit + 1`, returns the first `limit` and a cursor when a next row exists.
- `set_facets` returns `disposition`, `states`, `municipalities` (top 20), `years`, `document_types` via `values().annotate(Count("id"))` on the lens-filtered items.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group test pytest tests/research -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add research/lens.py research/browse.py tests/research/test_lens.py tests/research/test_browse.py
git commit -m "feat: add research lens and set browsing"
```

---

### Task 4: Project and set views

**Files:**
- Create: `research/urls.py`, `research/views.py`
- Create: `templates/research/project_list.html`, `project_detail.html`, `set_detail.html`, `partials/set_status.html`, `partials/set_items.html`, `partials/facets.html`
- Modify: `config/urls.py`
- Modify: `templates/base.html` (Research nav link, desktop + mobile)
- Test: `tests/research/test_views.py`

**Interfaces:**
- Consumes: `create_result_set`, `browse_set`, `set_facets`, `Lens`.
- Produces URL names under `research:`: `project-list`, `project-detail`, `set-create`, `set-detail`, `set-status`.

- [ ] **Step 1: Write the failing view tests**

Create `tests/research/test_views.py` with:
- unauthenticated `/research/` redirects to login;
- project list shows only the signed-in user's projects;
- another user's project/set returns 404;
- `set-create` requires a project the user owns and creates a READY set from a small query;
- set detail renders items and facets;
- status partial renders for pending sets and polling markup;
- keyset pagination via `?after=` returns the next page.

Use `MeetingPageFactory`/`MuniFactory` for pages and `project = ProjectFactory(owner=user)`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --group test pytest tests/research/test_views.py -v`
Expected: FAIL (404 / no URLs).

- [ ] **Step 3: Implement views, URLs and templates**

`research/views.py`:
- `ProjectListView(LoginRequiredMixin, ListView)` — `Project.objects.filter(owner=request.user)`, annotate set count.
- `ProjectDetailView(LoginRequiredMixin, DetailView)` — owner-scoped; sets ordered `-created`.
- `SetCreateView(LoginRequiredMixin, View)` — POST only; resolves an owner-scoped `Project`; builds `QuerySpec` from hidden search params (`q`, `meeting_name`, `states`, `municipality`, `date_from`, `date_to`, `document_type`); calls `create_result_set`; redirects to `set-detail`.
- `SetDetailView(LoginRequiredMixin, DetailView)` — owner-scoped; parses `Lens.from_querydict(request.GET)`, calls `browse_set` and `set_facets`, renders items + facets + next link.
- `SetStatusView(LoginRequiredMixin, View)` — owner-scoped; returns `partials/set_status.html`; when not READY renders `hx-get` with `hx-trigger="every 2s"` and `hx-swap="outerHTML"`.

`research/urls.py` mirrors the design's Phase 1A subset:

```python
app_name = "research"
urlpatterns = [
    path("", ProjectListView.as_view(), name="project-list"),
    path("<uuid:project>/", ProjectDetailView.as_view(), name="project-detail"),
    path("<uuid:project>/sets/new/", SetCreateView.as_view(), name="set-create"),
    path("<uuid:set>/", SetDetailView.as_view(), name="set-detail"),
    path("<uuid:set>/status/", SetStatusView.as_view(), name="set-status"),
]
```

Mount at `path("research/", include("research.urls"))` in `config/urls.py`; add "Research" to the desktop and mobile nav in `templates/base.html` (authenticated only).

Templates follow existing conventions: extend `base.html`, Tailwind cards, `LoginRequired` pages owner-scoped, no inline JS beyond Alpine for a filter form. Set detail shows the facet sidebar, the item cards (meeting name/date, document type, page number, disposition badge), and a "Next" link carrying `after`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group test pytest tests/research -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add research/urls.py research/views.py templates/research config/urls.py templates/base.html tests/research/test_views.py
git commit -m "feat: add research project and set views"
```

---

## Phase 1A done when

- `research` models migrate cleanly and pass constraint/FK tests.
- A query spec materializes into a frozen `ResultSet` of `SetItem`s (inline or on the `research` queue), is idempotent, and records an audit event; over-ceiling sets are `TOO_LARGE`.
- Set detail filters and paginates entirely on `research_setitem`, with keyset paging that handles `rank IS NULL`.
- Project/set/status views are owner-scoped, and the suite passes: `uv run --group test pytest`.
