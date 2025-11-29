# Notebooks Feature Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add notebooks functionality allowing users to save MeetingPages from search results to named collections with notes and tags.

**Architecture:** New `notebooks` Django app with Notebook, NotebookEntry, Tag, and NotebookEntryTag models. HTMX-powered save buttons in search results. Standard Django CBVs for CRUD operations.

**Tech Stack:** Django 5.2, HTMX, Alpine.js, Tailwind CSS, PostgreSQL, pytest with factory_boy

---

## Task 1: Create notebooks Django app

**Files:**
- Create: `notebooks/__init__.py`
- Create: `notebooks/apps.py`
- Create: `notebooks/admin.py`
- Modify: `config/settings/base.py:33-38`

**Step 1: Create the notebooks app directory structure**

```bash
mkdir -p notebooks
touch notebooks/__init__.py
```

**Step 2: Create apps.py**

```python
# notebooks/apps.py
from django.apps import AppConfig


class NotebooksConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "notebooks"
```

**Step 3: Create empty admin.py**

```python
# notebooks/admin.py
from django.contrib import admin

# Register your models here.
```

**Step 4: Add notebooks to INSTALLED_APPS**

In `config/settings/base.py`, update LOCAL_APPS:

```python
LOCAL_APPS: list[str] = [
    "users",
    "municipalities",
    "searches",
    "meetings",
    "notebooks",
]
```

**Step 5: Verify app loads**

```bash
uv run python manage.py check
```

Expected: System check identified no issues.

**Step 6: Commit**

```bash
git add notebooks/ config/settings/base.py
git commit -m "feat(notebooks): create notebooks Django app"
```

---

## Task 2: Create Notebook model with tests

**Files:**
- Create: `notebooks/models.py`
- Create: `tests/notebooks/__init__.py`
- Create: `tests/notebooks/test_models.py`
- Modify: `tests/factories.py`

**Step 1: Create test directory**

```bash
mkdir -p tests/notebooks
touch tests/notebooks/__init__.py
```

**Step 2: Write failing test for Notebook model**

```python
# tests/notebooks/test_models.py
import pytest

from tests.factories import NotebookFactory, UserFactory


@pytest.mark.django_db
class TestNotebookModel:
    def test_create_notebook(self):
        """Test basic notebook creation."""
        user = UserFactory()
        notebook = NotebookFactory(user=user, name="My Research")

        assert notebook.name == "My Research"
        assert notebook.user == user
        assert notebook.is_archived is False
        assert notebook.created is not None
        assert notebook.modified is not None
        assert str(notebook.id)  # UUID is valid

    def test_notebook_str_representation(self):
        """Test string representation."""
        notebook = NotebookFactory(name="Housing Research")
        assert "Housing Research" in str(notebook)

    def test_notebook_is_archived_default_false(self):
        """Test that is_archived defaults to False."""
        notebook = NotebookFactory()
        assert notebook.is_archived is False

    def test_notebook_ordering_by_modified_desc(self):
        """Test that notebooks are ordered by modified date descending."""
        from notebooks.models import Notebook

        user = UserFactory()
        notebook1 = NotebookFactory(user=user, name="First")
        notebook2 = NotebookFactory(user=user, name="Second")

        # Touch notebook1 to make it more recently modified
        notebook1.name = "First Updated"
        notebook1.save()

        notebooks = list(Notebook.objects.filter(user=user))
        assert notebooks[0].name == "First Updated"
        assert notebooks[1].name == "Second"
```

**Step 3: Run test to verify it fails**

```bash
uv run pytest tests/notebooks/test_models.py -v
```

Expected: FAIL with ImportError (NotebookFactory not found)

**Step 4: Add NotebookFactory to factories.py**

Add to `tests/factories.py`:

```python
from notebooks.models import Notebook


class NotebookFactory(DjangoModelFactory):
    class Meta:
        model = Notebook

    user = factory.SubFactory(UserFactory)
    name = factory.Faker("sentence", nb_words=3)
    is_archived = False
```

**Step 5: Run test to verify it still fails**

```bash
uv run pytest tests/notebooks/test_models.py -v
```

Expected: FAIL with ImportError (Notebook model not found)

**Step 6: Create Notebook model**

```python
# notebooks/models.py
import uuid

from django.conf import settings
from django.db import models
from model_utils.models import TimeStampedModel


class Notebook(TimeStampedModel):
    """
    A collection of saved meeting pages belonging to a user.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notebooks",
    )
    name = models.CharField(max_length=200)
    is_archived = models.BooleanField(default=False)

    class Meta:
        verbose_name = "Notebook"
        verbose_name_plural = "Notebooks"
        ordering = ["-modified"]

    def __str__(self) -> str:
        return self.name
```

**Step 7: Create and run migration**

```bash
uv run python manage.py makemigrations notebooks
uv run python manage.py migrate
```

**Step 8: Run test to verify it passes**

```bash
uv run pytest tests/notebooks/test_models.py -v
```

Expected: PASS

**Step 9: Commit**

```bash
git add notebooks/models.py tests/notebooks/ tests/factories.py notebooks/migrations/
git commit -m "feat(notebooks): add Notebook model with tests"
```

---

## Task 3: Create Tag model with tests

**Files:**
- Modify: `notebooks/models.py`
- Modify: `tests/notebooks/test_models.py`
- Modify: `tests/factories.py`

**Step 1: Write failing test for Tag model**

Add to `tests/notebooks/test_models.py`:

```python
from tests.factories import TagFactory


@pytest.mark.django_db
class TestTagModel:
    def test_create_tag(self):
        """Test basic tag creation."""
        user = UserFactory()
        tag = TagFactory(user=user, name="budget")

        assert tag.name == "budget"
        assert tag.user == user
        assert tag.team is None
        assert str(tag.id)

    def test_tag_str_representation(self):
        """Test string representation."""
        tag = TagFactory(name="transportation")
        assert str(tag) == "transportation"

    def test_tag_unique_per_user(self):
        """Test that tag names are unique per user."""
        from django.db import IntegrityError
        from notebooks.models import Tag

        user = UserFactory()
        TagFactory(user=user, name="budget")

        with pytest.raises(IntegrityError):
            Tag.objects.create(user=user, name="budget")

    def test_different_users_can_have_same_tag_name(self):
        """Test that different users can have tags with same name."""
        user1 = UserFactory()
        user2 = UserFactory()

        tag1 = TagFactory(user=user1, name="budget")
        tag2 = TagFactory(user=user2, name="budget")

        assert tag1.name == tag2.name
        assert tag1.user != tag2.user
```

**Step 2: Run test to verify it fails**

```bash
uv run pytest tests/notebooks/test_models.py::TestTagModel -v
```

Expected: FAIL with ImportError

**Step 3: Add TagFactory to factories.py**

Add to `tests/factories.py`:

```python
from notebooks.models import Notebook, Tag


class TagFactory(DjangoModelFactory):
    class Meta:
        model = Tag

    user = factory.SubFactory(UserFactory)
    name = factory.Faker("word")
```

**Step 4: Add Tag model to notebooks/models.py**

Add after Notebook class:

```python
class Tag(TimeStampedModel):
    """
    A tag for categorizing notebook entries.
    User-scoped now, team-scoped in future.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="tags",
        null=True,
        blank=True,
    )
    # Future: team = models.ForeignKey("teams.Team", null=True, blank=True, ...)

    class Meta:
        verbose_name = "Tag"
        verbose_name_plural = "Tags"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["name", "user"],
                name="unique_tag_per_user",
            ),
        ]

    def __str__(self) -> str:
        return self.name
```

**Step 5: Create and run migration**

```bash
uv run python manage.py makemigrations notebooks
uv run python manage.py migrate
```

**Step 6: Run tests to verify they pass**

```bash
uv run pytest tests/notebooks/test_models.py -v
```

Expected: All PASS

**Step 7: Commit**

```bash
git add notebooks/models.py tests/notebooks/test_models.py tests/factories.py notebooks/migrations/
git commit -m "feat(notebooks): add Tag model with unique constraint per user"
```

---

## Task 4: Create NotebookEntry model with tests

**Files:**
- Modify: `notebooks/models.py`
- Modify: `tests/notebooks/test_models.py`
- Modify: `tests/factories.py`

**Step 1: Write failing test for NotebookEntry model**

Add to `tests/notebooks/test_models.py`:

```python
from tests.factories import (
    MeetingDocumentFactory,
    MeetingPageFactory,
    NotebookEntryFactory,
    NotebookFactory,
    TagFactory,
    UserFactory,
)


@pytest.mark.django_db
class TestNotebookEntryModel:
    def test_create_entry(self):
        """Test basic entry creation."""
        notebook = NotebookFactory()
        doc = MeetingDocumentFactory()
        page = MeetingPageFactory(document=doc)

        entry = NotebookEntryFactory(notebook=notebook, meeting_page=page)

        assert entry.notebook == notebook
        assert entry.meeting_page == page
        assert entry.note == ""
        assert str(entry.id)

    def test_entry_with_note(self):
        """Test entry with optional note."""
        entry = NotebookEntryFactory(note="Important budget discussion")
        assert entry.note == "Important budget discussion"

    def test_entry_with_tags(self):
        """Test entry with tags."""
        user = UserFactory()
        notebook = NotebookFactory(user=user)
        entry = NotebookEntryFactory(notebook=notebook)

        tag1 = TagFactory(user=user, name="budget")
        tag2 = TagFactory(user=user, name="housing")

        entry.tags.add(tag1, tag2)

        assert entry.tags.count() == 2
        assert tag1 in entry.tags.all()
        assert tag2 in entry.tags.all()

    def test_entry_unique_page_per_notebook(self):
        """Test that same page cannot be in same notebook twice."""
        from django.db import IntegrityError
        from notebooks.models import NotebookEntry

        notebook = NotebookFactory()
        doc = MeetingDocumentFactory()
        page = MeetingPageFactory(document=doc)

        NotebookEntryFactory(notebook=notebook, meeting_page=page)

        with pytest.raises(IntegrityError):
            NotebookEntry.objects.create(notebook=notebook, meeting_page=page)

    def test_same_page_in_different_notebooks(self):
        """Test that same page can be in different notebooks."""
        user = UserFactory()
        notebook1 = NotebookFactory(user=user, name="Research 1")
        notebook2 = NotebookFactory(user=user, name="Research 2")
        doc = MeetingDocumentFactory()
        page = MeetingPageFactory(document=doc)

        entry1 = NotebookEntryFactory(notebook=notebook1, meeting_page=page)
        entry2 = NotebookEntryFactory(notebook=notebook2, meeting_page=page)

        assert entry1.meeting_page == entry2.meeting_page
        assert entry1.notebook != entry2.notebook

    def test_entry_ordering_by_created_desc(self):
        """Test entries ordered by creation date descending."""
        from notebooks.models import NotebookEntry

        notebook = NotebookFactory()
        doc = MeetingDocumentFactory()
        page1 = MeetingPageFactory(document=doc, page_number=1)
        page2 = MeetingPageFactory(document=doc, page_number=2)

        entry1 = NotebookEntryFactory(notebook=notebook, meeting_page=page1)
        entry2 = NotebookEntryFactory(notebook=notebook, meeting_page=page2)

        entries = list(NotebookEntry.objects.filter(notebook=notebook))
        assert entries[0] == entry2  # More recent first
        assert entries[1] == entry1
```

**Step 2: Run test to verify it fails**

```bash
uv run pytest tests/notebooks/test_models.py::TestNotebookEntryModel -v
```

Expected: FAIL with ImportError

**Step 3: Add NotebookEntryFactory to factories.py**

Add to `tests/factories.py`:

```python
from notebooks.models import Notebook, NotebookEntry, Tag


class NotebookEntryFactory(DjangoModelFactory):
    class Meta:
        model = NotebookEntry

    notebook = factory.SubFactory(NotebookFactory)
    meeting_page = factory.SubFactory(MeetingPageFactory)
    note = ""
```

**Step 4: Add NotebookEntry model to notebooks/models.py**

Add after Tag class:

```python
class NotebookEntry(TimeStampedModel):
    """
    A saved meeting page in a notebook with optional note and tags.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    notebook = models.ForeignKey(
        Notebook,
        on_delete=models.CASCADE,
        related_name="entries",
    )
    meeting_page = models.ForeignKey(
        "meetings.MeetingPage",
        on_delete=models.CASCADE,
        related_name="notebook_entries",
    )
    note = models.TextField(blank=True, default="")
    tags = models.ManyToManyField(Tag, blank=True, related_name="entries")

    class Meta:
        verbose_name = "Notebook Entry"
        verbose_name_plural = "Notebook Entries"
        ordering = ["-created"]
        constraints = [
            models.UniqueConstraint(
                fields=["notebook", "meeting_page"],
                name="unique_page_per_notebook",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.notebook.name}: {self.meeting_page}"
```

**Step 5: Create and run migration**

```bash
uv run python manage.py makemigrations notebooks
uv run python manage.py migrate
```

**Step 6: Run tests to verify they pass**

```bash
uv run pytest tests/notebooks/test_models.py -v
```

Expected: All PASS

**Step 7: Commit**

```bash
git add notebooks/models.py tests/notebooks/test_models.py tests/factories.py notebooks/migrations/
git commit -m "feat(notebooks): add NotebookEntry model with unique constraint"
```

---

## Task 5: Add admin registration for notebook models

**Files:**
- Modify: `notebooks/admin.py`

**Step 1: Write failing test for admin**

Create `tests/notebooks/test_admin.py`:

```python
import pytest
from django.urls import reverse

from tests.factories import AdminUserFactory, NotebookFactory


@pytest.mark.django_db
class TestNotebookAdmin:
    def test_notebook_list_accessible(self, client):
        """Test that notebook admin list is accessible."""
        admin = AdminUserFactory()
        client.force_login(admin)

        url = reverse("admin:notebooks_notebook_changelist")
        response = client.get(url)

        assert response.status_code == 200

    def test_notebook_detail_accessible(self, client):
        """Test that notebook admin detail is accessible."""
        admin = AdminUserFactory()
        client.force_login(admin)
        notebook = NotebookFactory()

        url = reverse("admin:notebooks_notebook_change", args=[notebook.pk])
        response = client.get(url)

        assert response.status_code == 200
```

**Step 2: Run test to verify it fails**

```bash
uv run pytest tests/notebooks/test_admin.py -v
```

Expected: FAIL (NoReverseMatch)

**Step 3: Register models in admin**

```python
# notebooks/admin.py
from django.contrib import admin

from .models import Notebook, NotebookEntry, Tag


@admin.register(Notebook)
class NotebookAdmin(admin.ModelAdmin):
    list_display = ["name", "user", "is_archived", "created", "modified"]
    list_filter = ["is_archived", "created"]
    search_fields = ["name", "user__email"]
    raw_id_fields = ["user"]


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ["name", "user", "created"]
    list_filter = ["created"]
    search_fields = ["name", "user__email"]
    raw_id_fields = ["user"]


@admin.register(NotebookEntry)
class NotebookEntryAdmin(admin.ModelAdmin):
    list_display = ["notebook", "meeting_page", "created"]
    list_filter = ["created", "notebook"]
    search_fields = ["notebook__name", "note"]
    raw_id_fields = ["notebook", "meeting_page"]
    filter_horizontal = ["tags"]
```

**Step 4: Run test to verify it passes**

```bash
uv run pytest tests/notebooks/test_admin.py -v
```

Expected: PASS

**Step 5: Commit**

```bash
git add notebooks/admin.py tests/notebooks/test_admin.py
git commit -m "feat(notebooks): add admin registration for all models"
```

---

## Task 6: Create notebook list view with tests

**Files:**
- Create: `notebooks/views.py`
- Create: `notebooks/urls.py`
- Modify: `config/urls.py`
- Create: `templates/notebooks/notebook_list.html`
- Create: `tests/notebooks/test_views.py`

**Step 1: Write failing test for notebook list view**

```python
# tests/notebooks/test_views.py
import pytest
from django.urls import reverse

from tests.factories import NotebookFactory, UserFactory


@pytest.mark.django_db
class TestNotebookListView:
    def test_requires_login(self, client):
        """Test that unauthenticated users are redirected to login."""
        url = reverse("notebooks:notebook-list")
        response = client.get(url)

        assert response.status_code == 302
        assert "/login" in response.url or "/stagedoor" in response.url

    def test_shows_user_notebooks(self, client):
        """Test that view shows only user's notebooks."""
        user = UserFactory()
        other_user = UserFactory()

        my_notebook = NotebookFactory(user=user, name="My Research")
        _other_notebook = NotebookFactory(user=other_user, name="Other Research")

        client.force_login(user)
        url = reverse("notebooks:notebook-list")
        response = client.get(url)

        assert response.status_code == 200
        assert "My Research" in response.content.decode()
        assert "Other Research" not in response.content.decode()

    def test_hides_archived_by_default(self, client):
        """Test that archived notebooks are hidden by default."""
        user = UserFactory()
        NotebookFactory(user=user, name="Active Notebook", is_archived=False)
        NotebookFactory(user=user, name="Archived Notebook", is_archived=True)

        client.force_login(user)
        url = reverse("notebooks:notebook-list")
        response = client.get(url)

        content = response.content.decode()
        assert "Active Notebook" in content
        assert "Archived Notebook" not in content

    def test_shows_archived_with_param(self, client):
        """Test that archived notebooks shown when requested."""
        user = UserFactory()
        NotebookFactory(user=user, name="Active Notebook", is_archived=False)
        NotebookFactory(user=user, name="Archived Notebook", is_archived=True)

        client.force_login(user)
        url = reverse("notebooks:notebook-list")
        response = client.get(url + "?show_archived=1")

        content = response.content.decode()
        assert "Active Notebook" in content
        assert "Archived Notebook" in content

    def test_empty_state(self, client):
        """Test empty state message when no notebooks."""
        user = UserFactory()
        client.force_login(user)

        url = reverse("notebooks:notebook-list")
        response = client.get(url)

        assert "No notebooks yet" in response.content.decode()
```

**Step 2: Run test to verify it fails**

```bash
uv run pytest tests/notebooks/test_views.py::TestNotebookListView -v
```

Expected: FAIL (NoReverseMatch)

**Step 3: Create notebooks/urls.py**

```python
# notebooks/urls.py
from django.urls import path

from . import views

app_name = "notebooks"

urlpatterns = [
    path("", views.NotebookListView.as_view(), name="notebook-list"),
]
```

**Step 4: Create notebooks/views.py**

```python
# notebooks/views.py
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, QuerySet
from django.views.generic import ListView

from .models import Notebook


class NotebookListView(LoginRequiredMixin, ListView):
    model = Notebook
    template_name = "notebooks/notebook_list.html"
    context_object_name = "notebooks"

    def get_queryset(self) -> QuerySet[Notebook]:
        qs = Notebook.objects.filter(user=self.request.user).annotate(
            entry_count=Count("entries")
        )

        if not self.request.GET.get("show_archived"):
            qs = qs.filter(is_archived=False)

        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["show_archived"] = bool(self.request.GET.get("show_archived"))
        return context
```

**Step 5: Add notebooks URLs to config/urls.py**

Find the urlpatterns list and add:

```python
path("notebooks/", include("notebooks.urls")),
```

**Step 6: Create template**

```html
<!-- templates/notebooks/notebook_list.html -->
{% extends "base.html" %}

{% block title %}Notebooks - CivicObserver{% endblock %}

{% block content %}
<div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
    <!-- Header -->
    <div class="sm:flex sm:items-center sm:justify-between">
        <div>
            <h1 class="text-3xl font-bold tracking-tight text-gray-900">Notebooks</h1>
            <p class="mt-2 text-sm text-gray-600">Save and organize meeting pages for your research</p>
        </div>
        <div class="mt-4 sm:mt-0 flex gap-3">
            <a href="{% url 'notebooks:notebook-create' %}"
               class="inline-flex items-center px-4 py-2 border border-transparent text-sm font-medium rounded-md shadow-sm text-white bg-indigo-600 hover:bg-indigo-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-indigo-500 transition-colors duration-200">
                <svg class="w-5 h-5 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 6v6m0 0v6m0-6h6m-6 0H6"></path>
                </svg>
                New Notebook
            </a>
        </div>
    </div>

    <!-- Archive Toggle -->
    <div class="mt-4">
        {% if show_archived %}
            <a href="{% url 'notebooks:notebook-list' %}" class="text-sm text-indigo-600 hover:text-indigo-800">
                Hide archived notebooks
            </a>
        {% else %}
            <a href="{% url 'notebooks:notebook-list' %}?show_archived=1" class="text-sm text-gray-600 hover:text-gray-800">
                Show archived notebooks
            </a>
        {% endif %}
    </div>

    {% if notebooks %}
        <div class="mt-8 space-y-4">
            {% for notebook in notebooks %}
                <div class="bg-white overflow-hidden shadow rounded-lg border border-gray-200 hover:shadow-md transition-shadow duration-200 {% if notebook.is_archived %}opacity-60{% endif %}">
                    <div class="px-4 py-5 sm:p-6">
                        <div class="flex items-start justify-between">
                            <div class="flex-1 min-w-0">
                                <div class="flex items-center gap-3">
                                    <h3 class="text-lg font-medium leading-6 text-gray-900">
                                        <a href="{% url 'notebooks:notebook-detail' notebook.pk %}" class="hover:text-indigo-600">
                                            {{ notebook.name }}
                                        </a>
                                    </h3>
                                    {% if notebook.is_archived %}
                                        <span class="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-gray-100 text-gray-800">
                                            Archived
                                        </span>
                                    {% endif %}
                                </div>
                                <div class="mt-2 flex items-center gap-4 text-sm text-gray-500">
                                    <span>{{ notebook.entry_count }} page{{ notebook.entry_count|pluralize }}</span>
                                    <span>Updated {{ notebook.modified|timesince }} ago</span>
                                </div>
                            </div>
                            <div class="ml-4 flex-shrink-0 flex gap-2">
                                <a href="{% url 'notebooks:notebook-detail' notebook.pk %}"
                                   class="inline-flex items-center justify-center px-3 py-2 border border-gray-300 shadow-sm text-sm font-medium rounded-md text-gray-700 bg-white hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-indigo-500 transition-colors duration-200">
                                    View
                                </a>
                            </div>
                        </div>
                    </div>
                </div>
            {% endfor %}
        </div>
    {% else %}
        <div class="mt-8 text-center py-12 bg-white rounded-lg border border-gray-200">
            <svg class="mx-auto h-12 w-12 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253"></path>
            </svg>
            <h3 class="mt-4 text-lg font-medium text-gray-900">No notebooks yet</h3>
            <p class="mt-2 text-gray-500">Create your first notebook to start saving meeting pages.</p>
            <div class="mt-6">
                <a href="{% url 'notebooks:notebook-create' %}"
                   class="inline-flex items-center px-4 py-2 border border-transparent text-sm font-medium rounded-md shadow-sm text-white bg-indigo-600 hover:bg-indigo-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-indigo-500 transition-colors duration-200">
                    <svg class="w-5 h-5 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 6v6m0 0v6m0-6h6m-6 0H6"></path>
                    </svg>
                    Create Your First Notebook
                </a>
            </div>
        </div>
    {% endif %}
</div>
{% endblock content %}
```

**Step 7: Run test to verify it passes**

```bash
uv run pytest tests/notebooks/test_views.py::TestNotebookListView -v
```

Expected: All PASS

**Step 8: Commit**

```bash
git add notebooks/views.py notebooks/urls.py config/urls.py templates/notebooks/
git commit -m "feat(notebooks): add notebook list view"
```

---

## Task 7: Create notebook create view with tests

**Files:**
- Modify: `notebooks/views.py`
- Modify: `notebooks/urls.py`
- Create: `notebooks/forms.py`
- Create: `templates/notebooks/notebook_form.html`
- Modify: `tests/notebooks/test_views.py`

**Step 1: Write failing test**

Add to `tests/notebooks/test_views.py`:

```python
@pytest.mark.django_db
class TestNotebookCreateView:
    def test_requires_login(self, client):
        """Test that unauthenticated users are redirected."""
        url = reverse("notebooks:notebook-create")
        response = client.get(url)

        assert response.status_code == 302

    def test_get_shows_form(self, client):
        """Test GET request shows the form."""
        user = UserFactory()
        client.force_login(user)

        url = reverse("notebooks:notebook-create")
        response = client.get(url)

        assert response.status_code == 200
        assert "form" in response.context

    def test_post_creates_notebook(self, client):
        """Test POST creates a notebook for the user."""
        from notebooks.models import Notebook

        user = UserFactory()
        client.force_login(user)

        url = reverse("notebooks:notebook-create")
        response = client.post(url, {"name": "New Research"})

        assert response.status_code == 302
        assert Notebook.objects.filter(user=user, name="New Research").exists()

    def test_redirects_to_list_after_create(self, client):
        """Test successful creation redirects to list."""
        user = UserFactory()
        client.force_login(user)

        url = reverse("notebooks:notebook-create")
        response = client.post(url, {"name": "New Research"})

        assert response.status_code == 302
        assert reverse("notebooks:notebook-list") in response.url
```

**Step 2: Run test to verify it fails**

```bash
uv run pytest tests/notebooks/test_views.py::TestNotebookCreateView -v
```

Expected: FAIL

**Step 3: Create notebooks/forms.py**

```python
# notebooks/forms.py
from django import forms

from .models import Notebook


class NotebookForm(forms.ModelForm):
    class Meta:
        model = Notebook
        fields = ["name"]
        widgets = {
            "name": forms.TextInput(
                attrs={
                    "class": "block w-full rounded-md border-gray-300 shadow-sm focus:border-indigo-500 focus:ring-indigo-500 sm:text-sm",
                    "placeholder": "Enter notebook name",
                }
            ),
        }
```

**Step 4: Add CreateView to notebooks/views.py**

Add import and view:

```python
from django.urls import reverse_lazy
from django.views.generic import CreateView, ListView

from .forms import NotebookForm


class NotebookCreateView(LoginRequiredMixin, CreateView):
    model = Notebook
    form_class = NotebookForm
    template_name = "notebooks/notebook_form.html"
    success_url = reverse_lazy("notebooks:notebook-list")

    def form_valid(self, form):
        form.instance.user = self.request.user
        return super().form_valid(form)
```

**Step 5: Add URL**

Add to `notebooks/urls.py`:

```python
path("create/", views.NotebookCreateView.as_view(), name="notebook-create"),
```

**Step 6: Create template**

```html
<!-- templates/notebooks/notebook_form.html -->
{% extends "base.html" %}
{% load widget_tweaks %}

{% block title %}{% if object %}Edit{% else %}New{% endif %} Notebook - CivicObserver{% endblock %}

{% block content %}
<div class="max-w-2xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
    <div class="bg-white shadow rounded-lg">
        <div class="px-4 py-5 sm:p-6">
            <h1 class="text-2xl font-bold text-gray-900 mb-6">
                {% if object %}Edit Notebook{% else %}Create New Notebook{% endif %}
            </h1>

            <form method="post">
                {% csrf_token %}

                <div class="space-y-6">
                    <div>
                        <label for="id_name" class="block text-sm font-medium text-gray-700">
                            Name
                        </label>
                        <div class="mt-1">
                            {% render_field form.name class="block w-full rounded-md border-gray-300 shadow-sm focus:border-indigo-500 focus:ring-indigo-500 sm:text-sm" %}
                        </div>
                        {% if form.name.errors %}
                            <p class="mt-1 text-sm text-red-600">{{ form.name.errors.0 }}</p>
                        {% endif %}
                    </div>
                </div>

                <div class="mt-6 flex items-center justify-end gap-3">
                    <a href="{% url 'notebooks:notebook-list' %}"
                       class="inline-flex items-center px-4 py-2 border border-gray-300 text-sm font-medium rounded-md text-gray-700 bg-white hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-indigo-500">
                        Cancel
                    </a>
                    <button type="submit"
                            class="inline-flex items-center px-4 py-2 border border-transparent text-sm font-medium rounded-md shadow-sm text-white bg-indigo-600 hover:bg-indigo-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-indigo-500">
                        {% if object %}Save Changes{% else %}Create Notebook{% endif %}
                    </button>
                </div>
            </form>
        </div>
    </div>
</div>
{% endblock content %}
```

**Step 7: Run tests to verify they pass**

```bash
uv run pytest tests/notebooks/test_views.py -v
```

Expected: All PASS

**Step 8: Commit**

```bash
git add notebooks/views.py notebooks/urls.py notebooks/forms.py templates/notebooks/notebook_form.html
git commit -m "feat(notebooks): add notebook create view"
```

---

## Task 8: Create notebook detail view with tests

**Files:**
- Modify: `notebooks/views.py`
- Modify: `notebooks/urls.py`
- Create: `templates/notebooks/notebook_detail.html`
- Modify: `tests/notebooks/test_views.py`

**Step 1: Write failing test**

Add to `tests/notebooks/test_views.py`:

```python
from tests.factories import (
    MeetingDocumentFactory,
    MeetingPageFactory,
    NotebookEntryFactory,
    NotebookFactory,
    UserFactory,
)


@pytest.mark.django_db
class TestNotebookDetailView:
    def test_requires_login(self, client):
        """Test unauthenticated users are redirected."""
        notebook = NotebookFactory()
        url = reverse("notebooks:notebook-detail", args=[notebook.pk])
        response = client.get(url)

        assert response.status_code == 302

    def test_shows_notebook_entries(self, client):
        """Test view shows notebook entries."""
        user = UserFactory()
        notebook = NotebookFactory(user=user, name="My Research")
        doc = MeetingDocumentFactory(meeting_name="CityCouncil")
        page = MeetingPageFactory(document=doc, text="Budget discussion")
        NotebookEntryFactory(notebook=notebook, meeting_page=page, note="Important!")

        client.force_login(user)
        url = reverse("notebooks:notebook-detail", args=[notebook.pk])
        response = client.get(url)

        content = response.content.decode()
        assert response.status_code == 200
        assert "My Research" in content
        assert "CityCouncil" in content
        assert "Important!" in content

    def test_cannot_view_other_users_notebook(self, client):
        """Test users cannot view other users' notebooks."""
        user = UserFactory()
        other_user = UserFactory()
        notebook = NotebookFactory(user=other_user)

        client.force_login(user)
        url = reverse("notebooks:notebook-detail", args=[notebook.pk])
        response = client.get(url)

        assert response.status_code == 404

    def test_empty_notebook_message(self, client):
        """Test empty notebook shows helpful message."""
        user = UserFactory()
        notebook = NotebookFactory(user=user)

        client.force_login(user)
        url = reverse("notebooks:notebook-detail", args=[notebook.pk])
        response = client.get(url)

        assert "No saved pages" in response.content.decode()
```

**Step 2: Run test to verify it fails**

```bash
uv run pytest tests/notebooks/test_views.py::TestNotebookDetailView -v
```

Expected: FAIL

**Step 3: Add DetailView to notebooks/views.py**

Add import and view:

```python
from django.views.generic import CreateView, DetailView, ListView


class NotebookDetailView(LoginRequiredMixin, DetailView):
    model = Notebook
    template_name = "notebooks/notebook_detail.html"
    context_object_name = "notebook"

    def get_queryset(self) -> QuerySet[Notebook]:
        return Notebook.objects.filter(user=self.request.user).prefetch_related(
            "entries__meeting_page__document__municipality",
            "entries__tags",
        )
```

**Step 4: Add URL**

Add to `notebooks/urls.py`:

```python
path("<uuid:pk>/", views.NotebookDetailView.as_view(), name="notebook-detail"),
```

**Step 5: Create template**

```html
<!-- templates/notebooks/notebook_detail.html -->
{% extends "base.html" %}

{% block title %}{{ notebook.name }} - CivicObserver{% endblock %}

{% block content %}
<div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
    <!-- Header -->
    <div class="sm:flex sm:items-center sm:justify-between">
        <div>
            <nav class="text-sm text-gray-500 mb-2">
                <a href="{% url 'notebooks:notebook-list' %}" class="hover:text-indigo-600">Notebooks</a>
                <span class="mx-2">/</span>
                <span class="text-gray-900">{{ notebook.name }}</span>
            </nav>
            <h1 class="text-3xl font-bold tracking-tight text-gray-900">{{ notebook.name }}</h1>
            <p class="mt-2 text-sm text-gray-600">
                {{ notebook.entries.count }} page{{ notebook.entries.count|pluralize }}
                · Last updated {{ notebook.modified|timesince }} ago
            </p>
        </div>
        <div class="mt-4 sm:mt-0 flex gap-3">
            <a href="{% url 'notebooks:notebook-edit' notebook.pk %}"
               class="inline-flex items-center px-4 py-2 border border-gray-300 text-sm font-medium rounded-md shadow-sm text-gray-700 bg-white hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-indigo-500 transition-colors duration-200">
                Edit
            </a>
            {% if notebook.is_archived %}
                <form method="post" action="{% url 'notebooks:notebook-archive' notebook.pk %}">
                    {% csrf_token %}
                    <button type="submit"
                            class="inline-flex items-center px-4 py-2 border border-gray-300 text-sm font-medium rounded-md shadow-sm text-gray-700 bg-white hover:bg-gray-50">
                        Unarchive
                    </button>
                </form>
            {% else %}
                <form method="post" action="{% url 'notebooks:notebook-archive' notebook.pk %}">
                    {% csrf_token %}
                    <button type="submit"
                            class="inline-flex items-center px-4 py-2 border border-gray-300 text-sm font-medium rounded-md shadow-sm text-gray-700 bg-white hover:bg-gray-50">
                        Archive
                    </button>
                </form>
            {% endif %}
        </div>
    </div>

    {% if notebook.entries.exists %}
        <div class="mt-8 space-y-4">
            {% for entry in notebook.entries.all %}
                <div class="bg-white shadow rounded-lg border border-gray-200 p-6">
                    <!-- Entry Header -->
                    <div class="flex items-start justify-between mb-3">
                        <div class="flex-1">
                            <h3 class="text-lg font-semibold text-gray-900">
                                {{ entry.meeting_page.document.meeting_name }}
                                <span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium {% if entry.meeting_page.document.document_type == 'agenda' %}bg-blue-100 text-blue-800{% else %}bg-green-100 text-green-800{% endif %} ml-2">
                                    {{ entry.meeting_page.document.document_type|capfirst }}
                                </span>
                            </h3>
                            <div class="mt-1 text-sm text-gray-500">
                                {{ entry.meeting_page.document.municipality.name }}, {{ entry.meeting_page.document.municipality.state }}
                                · {{ entry.meeting_page.document.meeting_date|date:"F d, Y" }}
                                · Page {{ entry.meeting_page.page_number }}
                            </div>
                        </div>
                        <div class="ml-4 flex-shrink-0 flex gap-2">
                            <a href="{% url 'notebooks:entry-edit' notebook.pk entry.pk %}"
                               class="text-sm text-indigo-600 hover:text-indigo-800">Edit</a>
                            <form method="post" action="{% url 'notebooks:entry-delete' notebook.pk entry.pk %}" class="inline">
                                {% csrf_token %}
                                <button type="submit" class="text-sm text-red-600 hover:text-red-800">Remove</button>
                            </form>
                        </div>
                    </div>

                    <!-- Text Preview -->
                    <div class="text-sm text-gray-700 leading-relaxed mt-3">
                        {{ entry.meeting_page.text|truncatewords:50 }}
                    </div>

                    <!-- Note -->
                    {% if entry.note %}
                        <div class="mt-3 p-3 bg-yellow-50 rounded-md">
                            <p class="text-sm text-yellow-800">
                                <span class="font-medium">Note:</span> {{ entry.note }}
                            </p>
                        </div>
                    {% endif %}

                    <!-- Tags -->
                    {% if entry.tags.exists %}
                        <div class="mt-3 flex flex-wrap gap-2">
                            {% for tag in entry.tags.all %}
                                <span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-gray-100 text-gray-800">
                                    {{ tag.name }}
                                </span>
                            {% endfor %}
                        </div>
                    {% endif %}

                    <!-- View on CivicBand link -->
                    <div class="mt-4 pt-3 border-t border-gray-200">
                        <a href="https://{{ entry.meeting_page.document.municipality.subdomain }}.civic.band/meetings/{{ entry.meeting_page.document.civic_band_table_name }}/{{ entry.meeting_page.id }}"
                           target="_blank"
                           rel="noopener noreferrer"
                           class="text-sm text-indigo-600 hover:text-indigo-800">
                            View on CivicBand →
                        </a>
                    </div>
                </div>
            {% endfor %}
        </div>
    {% else %}
        <div class="mt-8 text-center py-12 bg-white rounded-lg border border-gray-200">
            <svg class="mx-auto h-12 w-12 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"></path>
            </svg>
            <h3 class="mt-4 text-lg font-medium text-gray-900">No saved pages yet</h3>
            <p class="mt-2 text-gray-500">Search for meetings and save pages to this notebook.</p>
            <div class="mt-6">
                <a href="{% url 'meetings:meeting-search' %}"
                   class="inline-flex items-center px-4 py-2 border border-transparent text-sm font-medium rounded-md shadow-sm text-white bg-indigo-600 hover:bg-indigo-700">
                    Search Meetings
                </a>
            </div>
        </div>
    {% endif %}
</div>
{% endblock content %}
```

**Step 6: Run tests to verify they pass**

```bash
uv run pytest tests/notebooks/test_views.py::TestNotebookDetailView -v
```

Expected: All PASS

**Step 7: Commit**

```bash
git add notebooks/views.py notebooks/urls.py templates/notebooks/notebook_detail.html
git commit -m "feat(notebooks): add notebook detail view"
```

---

## Task 9: Add notebook edit, archive, and delete views

**Files:**
- Modify: `notebooks/views.py`
- Modify: `notebooks/urls.py`
- Create: `templates/notebooks/notebook_confirm_delete.html`
- Modify: `tests/notebooks/test_views.py`

**Step 1: Write failing tests**

Add to `tests/notebooks/test_views.py`:

```python
@pytest.mark.django_db
class TestNotebookEditView:
    def test_can_edit_own_notebook(self, client):
        """Test user can edit their own notebook."""
        user = UserFactory()
        notebook = NotebookFactory(user=user, name="Old Name")

        client.force_login(user)
        url = reverse("notebooks:notebook-edit", args=[notebook.pk])
        response = client.post(url, {"name": "New Name"})

        notebook.refresh_from_db()
        assert response.status_code == 302
        assert notebook.name == "New Name"

    def test_cannot_edit_other_users_notebook(self, client):
        """Test user cannot edit another user's notebook."""
        user = UserFactory()
        other_user = UserFactory()
        notebook = NotebookFactory(user=other_user)

        client.force_login(user)
        url = reverse("notebooks:notebook-edit", args=[notebook.pk])
        response = client.get(url)

        assert response.status_code == 404


@pytest.mark.django_db
class TestNotebookArchiveView:
    def test_can_archive_notebook(self, client):
        """Test user can archive their notebook."""
        user = UserFactory()
        notebook = NotebookFactory(user=user, is_archived=False)

        client.force_login(user)
        url = reverse("notebooks:notebook-archive", args=[notebook.pk])
        response = client.post(url)

        notebook.refresh_from_db()
        assert response.status_code == 302
        assert notebook.is_archived is True

    def test_can_unarchive_notebook(self, client):
        """Test user can unarchive their notebook."""
        user = UserFactory()
        notebook = NotebookFactory(user=user, is_archived=True)

        client.force_login(user)
        url = reverse("notebooks:notebook-archive", args=[notebook.pk])
        response = client.post(url)

        notebook.refresh_from_db()
        assert response.status_code == 302
        assert notebook.is_archived is False


@pytest.mark.django_db
class TestNotebookDeleteView:
    def test_can_delete_own_notebook(self, client):
        """Test user can delete their own notebook."""
        from notebooks.models import Notebook

        user = UserFactory()
        notebook = NotebookFactory(user=user)
        notebook_pk = notebook.pk

        client.force_login(user)
        url = reverse("notebooks:notebook-delete", args=[notebook.pk])
        response = client.post(url)

        assert response.status_code == 302
        assert not Notebook.objects.filter(pk=notebook_pk).exists()

    def test_get_shows_confirmation(self, client):
        """Test GET shows delete confirmation."""
        user = UserFactory()
        notebook = NotebookFactory(user=user, name="To Delete")

        client.force_login(user)
        url = reverse("notebooks:notebook-delete", args=[notebook.pk])
        response = client.get(url)

        assert response.status_code == 200
        assert "To Delete" in response.content.decode()
```

**Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/notebooks/test_views.py::TestNotebookEditView tests/notebooks/test_views.py::TestNotebookArchiveView tests/notebooks/test_views.py::TestNotebookDeleteView -v
```

Expected: FAIL

**Step 3: Add views to notebooks/views.py**

Add imports and views:

```python
from django.shortcuts import get_object_or_404, redirect
from django.views import View
from django.views.generic import (
    CreateView,
    DeleteView,
    DetailView,
    ListView,
    UpdateView,
)


class NotebookEditView(LoginRequiredMixin, UpdateView):
    model = Notebook
    form_class = NotebookForm
    template_name = "notebooks/notebook_form.html"
    success_url = reverse_lazy("notebooks:notebook-list")

    def get_queryset(self) -> QuerySet[Notebook]:
        return Notebook.objects.filter(user=self.request.user)


class NotebookArchiveView(LoginRequiredMixin, View):
    def post(self, request, pk):
        notebook = get_object_or_404(Notebook, pk=pk, user=request.user)
        notebook.is_archived = not notebook.is_archived
        notebook.save()
        return redirect("notebooks:notebook-list")


class NotebookDeleteView(LoginRequiredMixin, DeleteView):
    model = Notebook
    template_name = "notebooks/notebook_confirm_delete.html"
    success_url = reverse_lazy("notebooks:notebook-list")

    def get_queryset(self) -> QuerySet[Notebook]:
        return Notebook.objects.filter(user=self.request.user)
```

**Step 4: Add URLs**

Add to `notebooks/urls.py`:

```python
path("<uuid:pk>/edit/", views.NotebookEditView.as_view(), name="notebook-edit"),
path(
    "<uuid:pk>/archive/", views.NotebookArchiveView.as_view(), name="notebook-archive"
),
path("<uuid:pk>/delete/", views.NotebookDeleteView.as_view(), name="notebook-delete"),
```

**Step 5: Create delete confirmation template**

```html
<!-- templates/notebooks/notebook_confirm_delete.html -->
{% extends "base.html" %}

{% block title %}Delete {{ notebook.name }} - CivicObserver{% endblock %}

{% block content %}
<div class="max-w-2xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
    <div class="bg-white shadow rounded-lg">
        <div class="px-4 py-5 sm:p-6">
            <h1 class="text-2xl font-bold text-gray-900 mb-4">Delete Notebook</h1>

            <div class="bg-red-50 border border-red-200 rounded-md p-4 mb-6">
                <div class="flex">
                    <svg class="h-5 w-5 text-red-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"></path>
                    </svg>
                    <div class="ml-3">
                        <h3 class="text-sm font-medium text-red-800">Warning</h3>
                        <p class="mt-1 text-sm text-red-700">
                            This will permanently delete the notebook "{{ notebook.name }}" and all {{ notebook.entries.count }} saved page{{ notebook.entries.count|pluralize }}. This action cannot be undone.
                        </p>
                    </div>
                </div>
            </div>

            <form method="post">
                {% csrf_token %}
                <div class="flex items-center justify-end gap-3">
                    <a href="{% url 'notebooks:notebook-detail' notebook.pk %}"
                       class="inline-flex items-center px-4 py-2 border border-gray-300 text-sm font-medium rounded-md text-gray-700 bg-white hover:bg-gray-50">
                        Cancel
                    </a>
                    <button type="submit"
                            class="inline-flex items-center px-4 py-2 border border-transparent text-sm font-medium rounded-md shadow-sm text-white bg-red-600 hover:bg-red-700">
                        Delete Notebook
                    </button>
                </div>
            </form>
        </div>
    </div>
</div>
{% endblock content %}
```

**Step 6: Run tests to verify they pass**

```bash
uv run pytest tests/notebooks/test_views.py -v
```

Expected: All PASS

**Step 7: Commit**

```bash
git add notebooks/views.py notebooks/urls.py templates/notebooks/notebook_confirm_delete.html
git commit -m "feat(notebooks): add notebook edit, archive, delete views"
```

---

## Task 10: Add save page to notebook endpoint (HTMX)

**Files:**
- Modify: `notebooks/views.py`
- Modify: `notebooks/urls.py`
- Create: `templates/notebooks/partials/save_button.html`
- Create: `templates/notebooks/partials/toast.html`
- Create: `tests/notebooks/test_save_page.py`

**Step 1: Write failing test**

```python
# tests/notebooks/test_save_page.py
import pytest
from django.urls import reverse

from notebooks.models import NotebookEntry
from tests.factories import (
    MeetingDocumentFactory,
    MeetingPageFactory,
    NotebookFactory,
    UserFactory,
)


@pytest.mark.django_db
class TestSavePageToNotebook:
    def test_requires_login(self, client):
        """Test unauthenticated users get 403."""
        url = reverse("notebooks:save-page")
        response = client.post(url, {"page_id": "test"})

        assert response.status_code == 302

    def test_saves_page_to_most_recent_notebook(self, client):
        """Test page saved to most recently used notebook."""
        user = UserFactory()
        notebook = NotebookFactory(user=user, name="My Research")
        doc = MeetingDocumentFactory()
        page = MeetingPageFactory(document=doc)

        client.force_login(user)
        url = reverse("notebooks:save-page")
        response = client.post(
            url,
            {"page_id": page.id},
            HTTP_HX_REQUEST="true",
        )

        assert response.status_code == 200
        assert NotebookEntry.objects.filter(
            notebook=notebook,
            meeting_page=page,
        ).exists()

    def test_creates_notebook_if_none_exist(self, client):
        """Test creates default notebook if user has none."""
        from notebooks.models import Notebook

        user = UserFactory()
        doc = MeetingDocumentFactory()
        page = MeetingPageFactory(document=doc)

        client.force_login(user)
        url = reverse("notebooks:save-page")
        response = client.post(
            url,
            {"page_id": page.id},
            HTTP_HX_REQUEST="true",
        )

        assert response.status_code == 200
        assert Notebook.objects.filter(user=user).exists()
        notebook = Notebook.objects.get(user=user)
        assert NotebookEntry.objects.filter(
            notebook=notebook, meeting_page=page
        ).exists()

    def test_duplicate_returns_already_saved_message(self, client):
        """Test saving same page twice returns already saved."""
        from tests.factories import NotebookEntryFactory

        user = UserFactory()
        notebook = NotebookFactory(user=user)
        doc = MeetingDocumentFactory()
        page = MeetingPageFactory(document=doc)
        NotebookEntryFactory(notebook=notebook, meeting_page=page)

        client.force_login(user)
        url = reverse("notebooks:save-page")
        response = client.post(
            url,
            {"page_id": page.id},
            HTTP_HX_REQUEST="true",
        )

        assert response.status_code == 200
        assert "Already in" in response.content.decode()

    def test_can_specify_target_notebook(self, client):
        """Test can save to specific notebook."""
        user = UserFactory()
        notebook1 = NotebookFactory(user=user, name="Research 1")
        notebook2 = NotebookFactory(user=user, name="Research 2")
        doc = MeetingDocumentFactory()
        page = MeetingPageFactory(document=doc)

        client.force_login(user)
        url = reverse("notebooks:save-page")
        response = client.post(
            url,
            {"page_id": page.id, "notebook_id": str(notebook2.id)},
            HTTP_HX_REQUEST="true",
        )

        assert response.status_code == 200
        assert NotebookEntry.objects.filter(
            notebook=notebook2, meeting_page=page
        ).exists()
        assert not NotebookEntry.objects.filter(
            notebook=notebook1, meeting_page=page
        ).exists()
```

**Step 2: Run test to verify it fails**

```bash
uv run pytest tests/notebooks/test_save_page.py -v
```

Expected: FAIL

**Step 3: Add SavePageView to notebooks/views.py**

Add view:

```python
from django.http import HttpResponse
from django.template.loader import render_to_string

from meetings.models import MeetingPage


class SavePageView(LoginRequiredMixin, View):
    """HTMX endpoint to save a meeting page to a notebook."""

    def post(self, request):
        page_id = request.POST.get("page_id")
        notebook_id = request.POST.get("notebook_id")

        # Get the page
        page = get_object_or_404(MeetingPage, id=page_id)

        # Get or create target notebook
        if notebook_id:
            notebook = get_object_or_404(Notebook, id=notebook_id, user=request.user)
        else:
            # Use most recently modified notebook, or create one
            notebook = (
                Notebook.objects.filter(user=request.user, is_archived=False)
                .order_by("-modified")
                .first()
            )
            if not notebook:
                notebook = Notebook.objects.create(
                    user=request.user,
                    name="My Notebook",
                )

        # Check if already saved
        existing = NotebookEntry.objects.filter(
            notebook=notebook,
            meeting_page=page,
        ).first()

        if existing:
            html = render_to_string(
                "notebooks/partials/toast.html",
                {
                    "message": f"Already in {notebook.name}",
                    "type": "info",
                    "page_id": page_id,
                    "is_saved": True,
                },
                request=request,
            )
            return HttpResponse(html)

        # Create entry
        NotebookEntry.objects.create(
            notebook=notebook,
            meeting_page=page,
        )

        # Update notebook's modified time
        notebook.save()

        html = render_to_string(
            "notebooks/partials/toast.html",
            {
                "message": f"Saved to {notebook.name}",
                "type": "success",
                "page_id": page_id,
                "is_saved": True,
                "notebook": notebook,
                "notebooks": Notebook.objects.filter(
                    user=request.user, is_archived=False
                ).exclude(id=notebook.id),
            },
            request=request,
        )
        return HttpResponse(html)
```

Add import at top:

```python
from .models import Notebook, NotebookEntry
```

**Step 4: Add URL**

Add to `notebooks/urls.py`:

```python
path("save-page/", views.SavePageView.as_view(), name="save-page"),
```

**Step 5: Create toast template**

```html
<!-- templates/notebooks/partials/toast.html -->
<div id="notebook-toast-{{ page_id }}"
     class="fixed bottom-4 right-4 z-50"
     x-data="{ show: true }"
     x-init="setTimeout(() => show = false, 4000)"
     x-show="show"
     x-transition:enter="transform ease-out duration-300 transition"
     x-transition:enter-start="translate-y-2 opacity-0"
     x-transition:enter-end="translate-y-0 opacity-100"
     x-transition:leave="transition ease-in duration-100"
     x-transition:leave-start="opacity-100"
     x-transition:leave-end="opacity-0">
    <div class="{% if type == 'success' %}bg-green-50 border-green-200{% elif type == 'info' %}bg-blue-50 border-blue-200{% else %}bg-gray-50 border-gray-200{% endif %} border rounded-lg shadow-lg p-4 flex items-center gap-3">
        {% if type == 'success' %}
            <svg class="h-5 w-5 text-green-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"></path>
            </svg>
        {% else %}
            <svg class="h-5 w-5 text-blue-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path>
            </svg>
        {% endif %}
        <span class="text-sm font-medium {% if type == 'success' %}text-green-800{% else %}text-blue-800{% endif %}">
            {{ message }}
        </span>
        {% if notebooks %}
            <div class="relative" x-data="{ open: false }">
                <button @click="open = !open" class="text-sm text-indigo-600 hover:text-indigo-800 underline">
                    Change
                </button>
                <div x-show="open"
                     @click.away="open = false"
                     class="absolute right-0 bottom-full mb-2 w-48 bg-white rounded-md shadow-lg border border-gray-200 py-1">
                    {% for nb in notebooks %}
                        <button hx-post="{% url 'notebooks:save-page' %}"
                                hx-vals='{"page_id": "{{ page_id }}", "notebook_id": "{{ nb.id }}"}'
                                hx-target="#notebook-toast-{{ page_id }}"
                                hx-swap="outerHTML"
                                class="block w-full text-left px-4 py-2 text-sm text-gray-700 hover:bg-gray-100">
                            {{ nb.name }}
                        </button>
                    {% endfor %}
                </div>
            </div>
        {% endif %}
    </div>
</div>
```

**Step 6: Create save button template**

```html
<!-- templates/notebooks/partials/save_button.html -->
<button hx-post="{% url 'notebooks:save-page' %}"
        hx-vals='{"page_id": "{{ page_id }}"}'
        hx-target="this"
        hx-swap="outerHTML"
        class="inline-flex items-center px-2 py-1 text-sm {% if is_saved %}text-indigo-600{% else %}text-gray-500 hover:text-indigo-600{% endif %} transition-colors"
        title="{% if is_saved %}Saved to notebook{% else %}Save to notebook{% endif %}">
    {% if is_saved %}
        <svg class="w-5 h-5" fill="currentColor" viewBox="0 0 20 20">
            <path d="M5 4a2 2 0 012-2h6a2 2 0 012 2v14l-5-2.5L5 18V4z"></path>
        </svg>
    {% else %}
        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 5a2 2 0 012-2h10a2 2 0 012 2v16l-7-3.5L5 21V5z"></path>
        </svg>
    {% endif %}
</button>
```

**Step 7: Run tests to verify they pass**

```bash
uv run pytest tests/notebooks/test_save_page.py -v
```

Expected: All PASS

**Step 8: Commit**

```bash
git add notebooks/views.py notebooks/urls.py templates/notebooks/partials/
git commit -m "feat(notebooks): add HTMX save page endpoint"
```

---

## Task 11: Integrate save button into search results

**Files:**
- Modify: `templates/meetings/partials/search_results.html`
- Modify: `meetings/views.py`
- Create: `tests/notebooks/test_search_integration.py`

**Step 1: Write failing test**

```python
# tests/notebooks/test_search_integration.py
import pytest
from django.urls import reverse

from tests.factories import (
    MeetingDocumentFactory,
    MeetingPageFactory,
    MuniFactory,
    NotebookEntryFactory,
    NotebookFactory,
    UserFactory,
)


@pytest.mark.django_db
class TestSearchResultsSaveButton:
    def test_save_button_appears_for_authenticated_users(self, client):
        """Test save button appears in search results for logged-in users."""
        user = UserFactory()
        muni = MuniFactory()
        doc = MeetingDocumentFactory(municipality=muni)
        MeetingPageFactory(document=doc, text="housing policy discussion")

        client.force_login(user)
        url = reverse("meetings:meeting-search-results")
        response = client.get(url, {"query": "housing"})

        assert response.status_code == 200
        assert "hx-post" in response.content.decode()
        assert "save-page" in response.content.decode()

    def test_save_button_shows_saved_state_for_already_saved(self, client):
        """Test save button shows filled state when page already saved."""
        user = UserFactory()
        notebook = NotebookFactory(user=user)
        muni = MuniFactory()
        doc = MeetingDocumentFactory(municipality=muni)
        page = MeetingPageFactory(document=doc, text="housing policy discussion")
        NotebookEntryFactory(notebook=notebook, meeting_page=page)

        client.force_login(user)
        url = reverse("meetings:meeting-search-results")
        response = client.get(url, {"query": "housing"})

        assert response.status_code == 200
        # Check for filled bookmark icon (saved state)
        assert 'fill="currentColor"' in response.content.decode()

    def test_no_save_button_for_anonymous_users(self, client):
        """Test save button not shown for anonymous users."""
        muni = MuniFactory()
        doc = MeetingDocumentFactory(municipality=muni)
        MeetingPageFactory(document=doc, text="housing policy discussion")

        url = reverse("meetings:meeting-search-results")
        response = client.get(url, {"query": "housing"})

        assert response.status_code == 200
        assert "save-page" not in response.content.decode()
```

**Step 2: Run test to verify it fails**

```bash
uv run pytest tests/notebooks/test_search_integration.py -v
```

Expected: FAIL

**Step 3: Modify meetings/views.py to include saved pages context**

Find the search results view and add to get_context_data or equivalent:

```python
# Add to the view that renders search results
if request.user.is_authenticated:
    from notebooks.models import NotebookEntry

    # Get page IDs that are saved to any of user's notebooks
    saved_page_ids = set(
        NotebookEntry.objects.filter(notebook__user=request.user).values_list(
            "meeting_page_id", flat=True
        )
    )
    context["saved_page_ids"] = saved_page_ids
else:
    context["saved_page_ids"] = set()
```

**Step 4: Modify search results template**

In `templates/meetings/partials/search_results.html`, find the actions section (around line 126-138) and add the save button:

```html
<!-- Actions -->
<div class="mt-4 flex items-center justify-between pt-4 border-t border-gray-200">
    <div class="flex space-x-2">
        {% if user.is_authenticated %}
            {% include "notebooks/partials/save_button.html" with page_id=result.id is_saved=result.id|in_set:saved_page_ids %}
        {% endif %}
        <a href="https://{{ result.document.municipality.subdomain }}.civic.band/meetings/{{ result.document.civic_band_table_name }}/{{ result.id }}"
           ...
        </a>
    </div>
    ...
</div>
```

**Step 5: Create template filter for checking set membership**

Create `notebooks/templatetags/__init__.py` and `notebooks/templatetags/notebook_filters.py`:

```python
# notebooks/templatetags/__init__.py
# (empty file)
```

```python
# notebooks/templatetags/notebook_filters.py
from django import template

register = template.Library()


@register.filter
def in_set(value, the_set):
    """Check if value is in a set."""
    return value in the_set
```

**Step 6: Load filter in template**

Add at top of `search_results.html`:

```html
{% load notebook_filters %}
```

**Step 7: Run tests to verify they pass**

```bash
uv run pytest tests/notebooks/test_search_integration.py -v
```

Expected: All PASS

**Step 8: Commit**

```bash
git add templates/meetings/partials/search_results.html meetings/views.py notebooks/templatetags/
git commit -m "feat(notebooks): integrate save button into search results"
```

---

## Task 12: Add entry edit view for notes and tags

**Files:**
- Modify: `notebooks/views.py`
- Modify: `notebooks/urls.py`
- Modify: `notebooks/forms.py`
- Create: `templates/notebooks/entry_form.html`
- Create: `tests/notebooks/test_entry_views.py`

**Step 1: Write failing test**

```python
# tests/notebooks/test_entry_views.py
import pytest
from django.urls import reverse

from tests.factories import (
    MeetingDocumentFactory,
    MeetingPageFactory,
    NotebookEntryFactory,
    NotebookFactory,
    TagFactory,
    UserFactory,
)


@pytest.mark.django_db
class TestEntryEditView:
    def test_requires_login(self, client):
        """Test unauthenticated users are redirected."""
        notebook = NotebookFactory()
        entry = NotebookEntryFactory(notebook=notebook)
        url = reverse("notebooks:entry-edit", args=[notebook.pk, entry.pk])
        response = client.get(url)

        assert response.status_code == 302

    def test_can_edit_own_entry(self, client):
        """Test user can edit entry in their notebook."""
        user = UserFactory()
        notebook = NotebookFactory(user=user)
        entry = NotebookEntryFactory(notebook=notebook, note="")

        client.force_login(user)
        url = reverse("notebooks:entry-edit", args=[notebook.pk, entry.pk])
        response = client.post(url, {"note": "Updated note"})

        entry.refresh_from_db()
        assert response.status_code == 302
        assert entry.note == "Updated note"

    def test_cannot_edit_other_users_entry(self, client):
        """Test user cannot edit entry in another user's notebook."""
        user = UserFactory()
        other_user = UserFactory()
        notebook = NotebookFactory(user=other_user)
        entry = NotebookEntryFactory(notebook=notebook)

        client.force_login(user)
        url = reverse("notebooks:entry-edit", args=[notebook.pk, entry.pk])
        response = client.get(url)

        assert response.status_code == 404

    def test_can_add_tags_to_entry(self, client):
        """Test user can add tags to entry."""
        user = UserFactory()
        notebook = NotebookFactory(user=user)
        entry = NotebookEntryFactory(notebook=notebook)
        tag = TagFactory(user=user, name="budget")

        client.force_login(user)
        url = reverse("notebooks:entry-edit", args=[notebook.pk, entry.pk])
        response = client.post(url, {"note": "", "tags": [tag.pk]})

        entry.refresh_from_db()
        assert response.status_code == 302
        assert tag in entry.tags.all()


@pytest.mark.django_db
class TestEntryDeleteView:
    def test_can_delete_own_entry(self, client):
        """Test user can delete entry from their notebook."""
        from notebooks.models import NotebookEntry

        user = UserFactory()
        notebook = NotebookFactory(user=user)
        entry = NotebookEntryFactory(notebook=notebook)
        entry_pk = entry.pk

        client.force_login(user)
        url = reverse("notebooks:entry-delete", args=[notebook.pk, entry.pk])
        response = client.post(url)

        assert response.status_code == 302
        assert not NotebookEntry.objects.filter(pk=entry_pk).exists()
```

**Step 2: Run test to verify it fails**

```bash
uv run pytest tests/notebooks/test_entry_views.py -v
```

Expected: FAIL

**Step 3: Add NotebookEntryForm to forms.py**

```python
from .models import Notebook, NotebookEntry, Tag


class NotebookEntryForm(forms.ModelForm):
    tags = forms.ModelMultipleChoiceField(
        queryset=Tag.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple(
            attrs={"class": "h-4 w-4 text-indigo-600 border-gray-300 rounded"}
        ),
    )

    class Meta:
        model = NotebookEntry
        fields = ["note", "tags"]
        widgets = {
            "note": forms.Textarea(
                attrs={
                    "rows": 4,
                    "class": "block w-full rounded-md border-gray-300 shadow-sm focus:border-indigo-500 focus:ring-indigo-500 sm:text-sm",
                    "placeholder": "Add a note about this page...",
                }
            ),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user:
            self.fields["tags"].queryset = Tag.objects.filter(user=user)
```

**Step 4: Add entry views to views.py**

```python
from .forms import NotebookEntryForm, NotebookForm


class EntryEditView(LoginRequiredMixin, UpdateView):
    model = NotebookEntry
    form_class = NotebookEntryForm
    template_name = "notebooks/entry_form.html"
    pk_url_kwarg = "entry_pk"

    def get_queryset(self) -> QuerySet[NotebookEntry]:
        return NotebookEntry.objects.filter(
            notebook__user=self.request.user,
            notebook_id=self.kwargs["pk"],
        )

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_success_url(self):
        return reverse_lazy(
            "notebooks:notebook-detail", kwargs={"pk": self.kwargs["pk"]}
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["notebook"] = get_object_or_404(
            Notebook, pk=self.kwargs["pk"], user=self.request.user
        )
        return context


class EntryDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk, entry_pk):
        entry = get_object_or_404(
            NotebookEntry,
            pk=entry_pk,
            notebook_id=pk,
            notebook__user=request.user,
        )
        entry.delete()
        return redirect("notebooks:notebook-detail", pk=pk)
```

**Step 5: Add URLs**

Add to `notebooks/urls.py`:

```python
path(
    "<uuid:pk>/entries/<uuid:entry_pk>/",
    views.EntryEditView.as_view(),
    name="entry-edit",
),
path(
    "<uuid:pk>/entries/<uuid:entry_pk>/delete/",
    views.EntryDeleteView.as_view(),
    name="entry-delete",
),
```

**Step 6: Create entry form template**

```html
<!-- templates/notebooks/entry_form.html -->
{% extends "base.html" %}
{% load widget_tweaks %}

{% block title %}Edit Entry - {{ notebook.name }} - CivicObserver{% endblock %}

{% block content %}
<div class="max-w-2xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
    <div class="bg-white shadow rounded-lg">
        <div class="px-4 py-5 sm:p-6">
            <nav class="text-sm text-gray-500 mb-4">
                <a href="{% url 'notebooks:notebook-list' %}" class="hover:text-indigo-600">Notebooks</a>
                <span class="mx-2">/</span>
                <a href="{% url 'notebooks:notebook-detail' notebook.pk %}" class="hover:text-indigo-600">{{ notebook.name }}</a>
                <span class="mx-2">/</span>
                <span class="text-gray-900">Edit Entry</span>
            </nav>

            <h1 class="text-2xl font-bold text-gray-900 mb-2">Edit Entry</h1>

            <!-- Entry preview -->
            <div class="mb-6 p-4 bg-gray-50 rounded-md">
                <h3 class="font-medium text-gray-900">
                    {{ object.meeting_page.document.meeting_name }}
                </h3>
                <p class="text-sm text-gray-500 mt-1">
                    {{ object.meeting_page.document.municipality.name }}
                    · {{ object.meeting_page.document.meeting_date|date:"F d, Y" }}
                    · Page {{ object.meeting_page.page_number }}
                </p>
            </div>

            <form method="post">
                {% csrf_token %}

                <div class="space-y-6">
                    <div>
                        <label for="id_note" class="block text-sm font-medium text-gray-700">
                            Note
                        </label>
                        <div class="mt-1">
                            {% render_field form.note class="block w-full rounded-md border-gray-300 shadow-sm focus:border-indigo-500 focus:ring-indigo-500 sm:text-sm" %}
                        </div>
                    </div>

                    {% if form.tags.field.queryset.exists %}
                        <div>
                            <label class="block text-sm font-medium text-gray-700 mb-2">
                                Tags
                            </label>
                            <div class="space-y-2">
                                {% for tag in form.tags %}
                                    <div class="flex items-center">
                                        {{ tag.tag }}
                                        <label for="{{ tag.id_for_label }}" class="ml-2 text-sm text-gray-700">
                                            {{ tag.choice_label }}
                                        </label>
                                    </div>
                                {% endfor %}
                            </div>
                        </div>
                    {% endif %}
                </div>

                <div class="mt-6 flex items-center justify-end gap-3">
                    <a href="{% url 'notebooks:notebook-detail' notebook.pk %}"
                       class="inline-flex items-center px-4 py-2 border border-gray-300 text-sm font-medium rounded-md text-gray-700 bg-white hover:bg-gray-50">
                        Cancel
                    </a>
                    <button type="submit"
                            class="inline-flex items-center px-4 py-2 border border-transparent text-sm font-medium rounded-md shadow-sm text-white bg-indigo-600 hover:bg-indigo-700">
                        Save Changes
                    </button>
                </div>
            </form>
        </div>
    </div>
</div>
{% endblock content %}
```

**Step 7: Run tests to verify they pass**

```bash
uv run pytest tests/notebooks/test_entry_views.py -v
```

Expected: All PASS

**Step 8: Commit**

```bash
git add notebooks/views.py notebooks/urls.py notebooks/forms.py templates/notebooks/entry_form.html
git commit -m "feat(notebooks): add entry edit and delete views"
```

---

## Task 13: Add tag CRUD endpoints

**Files:**
- Modify: `notebooks/views.py`
- Modify: `notebooks/urls.py`
- Create: `tests/notebooks/test_tags.py`

**Step 1: Write failing test**

```python
# tests/notebooks/test_tags.py
import pytest
from django.urls import reverse

from notebooks.models import Tag
from tests.factories import TagFactory, UserFactory


@pytest.mark.django_db
class TestTagListView:
    def test_returns_user_tags(self, client):
        """Test returns user's tags as JSON."""
        user = UserFactory()
        TagFactory(user=user, name="budget")
        TagFactory(user=user, name="housing")

        # Other user's tags shouldn't appear
        other_user = UserFactory()
        TagFactory(user=other_user, name="zoning")

        client.force_login(user)
        url = reverse("notebooks:tag-list")
        response = client.get(url, HTTP_HX_REQUEST="true")

        assert response.status_code == 200
        content = response.content.decode()
        assert "budget" in content
        assert "housing" in content
        assert "zoning" not in content


@pytest.mark.django_db
class TestTagCreateView:
    def test_creates_tag(self, client):
        """Test creates new tag for user."""
        user = UserFactory()

        client.force_login(user)
        url = reverse("notebooks:tag-create")
        response = client.post(
            url,
            {"name": "transportation"},
            HTTP_HX_REQUEST="true",
        )

        assert response.status_code == 200
        assert Tag.objects.filter(user=user, name="transportation").exists()

    def test_returns_existing_if_duplicate(self, client):
        """Test returns existing tag if name already exists."""
        user = UserFactory()
        existing = TagFactory(user=user, name="budget")

        client.force_login(user)
        url = reverse("notebooks:tag-create")
        response = client.post(
            url,
            {"name": "budget"},
            HTTP_HX_REQUEST="true",
        )

        assert response.status_code == 200
        # Should not create duplicate
        assert Tag.objects.filter(user=user, name="budget").count() == 1
```

**Step 2: Run test to verify it fails**

```bash
uv run pytest tests/notebooks/test_tags.py -v
```

Expected: FAIL

**Step 3: Add tag views to views.py**

```python
from django.http import HttpResponse, JsonResponse

from .models import Notebook, NotebookEntry, Tag


class TagListView(LoginRequiredMixin, View):
    """Returns user's tags for autocomplete."""

    def get(self, request):
        tags = Tag.objects.filter(user=request.user).order_by("name")
        html = render_to_string(
            "notebooks/partials/tag_options.html",
            {"tags": tags},
            request=request,
        )
        return HttpResponse(html)


class TagCreateView(LoginRequiredMixin, View):
    """Creates a new tag or returns existing one."""

    def post(self, request):
        name = request.POST.get("name", "").strip().lower()
        if not name:
            return HttpResponse("", status=400)

        tag, created = Tag.objects.get_or_create(
            user=request.user,
            name=name,
        )

        html = render_to_string(
            "notebooks/partials/tag_chip.html",
            {"tag": tag, "created": created},
            request=request,
        )
        return HttpResponse(html)
```

**Step 4: Add URLs**

Add to `notebooks/urls.py`:

```python
path("tags/", views.TagListView.as_view(), name="tag-list"),
path("tags/create/", views.TagCreateView.as_view(), name="tag-create"),
```

**Step 5: Create tag templates**

```html
<!-- templates/notebooks/partials/tag_options.html -->
{% if tags %}
    {% for tag in tags %}
        <div class="flex items-center px-3 py-2 hover:bg-gray-100 cursor-pointer"
             @click="selectTag('{{ tag.id }}', '{{ tag.name }}')">
            <span class="text-sm text-gray-700">{{ tag.name }}</span>
        </div>
    {% endfor %}
{% else %}
    <div class="px-3 py-2 text-sm text-gray-500">
        No tags yet. Type to create one.
    </div>
{% endif %}
```

```html
<!-- templates/notebooks/partials/tag_chip.html -->
<span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-indigo-100 text-indigo-800">
    {{ tag.name }}
    <button type="button"
            class="ml-1 text-indigo-600 hover:text-indigo-800"
            @click="removeTag('{{ tag.id }}')">
        <svg class="h-3 w-3" fill="currentColor" viewBox="0 0 20 20">
            <path fill-rule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clip-rule="evenodd"></path>
        </svg>
    </button>
    <input type="hidden" name="tags" value="{{ tag.id }}">
</span>
```

**Step 6: Run tests to verify they pass**

```bash
uv run pytest tests/notebooks/test_tags.py -v
```

Expected: All PASS

**Step 7: Commit**

```bash
git add notebooks/views.py notebooks/urls.py templates/notebooks/partials/tag_options.html templates/notebooks/partials/tag_chip.html
git commit -m "feat(notebooks): add tag list and create endpoints"
```

---

## Task 14: Add notebooks link to navigation

**Files:**
- Modify: `templates/base.html`

**Step 1: Update base.html navigation**

Find the desktop navigation section (around line 43-49) and add notebooks link:

```html
<a href="{% url 'notebooks:notebook-list' %}" class="text-gray-600 hover:text-indigo-600 font-medium transition-colors">Notebooks</a>
```

Add after "Saved Searches" link.

Also update mobile navigation (around line 85-91):

```html
<a href="{% url 'notebooks:notebook-list' %}" class="block px-3 py-2 rounded-md text-base font-medium text-gray-600 hover:text-indigo-600 hover:bg-gray-50 transition-colors">Notebooks</a>
```

**Step 2: Verify navigation works**

```bash
uv run python manage.py runserver
```

Navigate to the site and verify Notebooks link appears and works.

**Step 3: Commit**

```bash
git add templates/base.html
git commit -m "feat(notebooks): add notebooks link to navigation"
```

---

## Task 15: Run full test suite and update coverage

**Files:**
- Modify: `pyproject.toml` (add notebooks to coverage)

**Step 1: Add notebooks to pytest coverage**

In `pyproject.toml`, find the addopts section and add `--cov=notebooks`:

```toml
addopts = [
    "--reuse-db",
    "--cov=src",
    "--cov=users",
    "--cov=municipalities",
    "--cov=searches",
    "--cov=meetings",
    "--cov=notebooks",
    "--cov-report=term-missing",
    "--cov-report=html",
    "--cov-fail-under=70",
    "--strict-markers",
    "--disable-warnings",
]
```

Also add to testpaths:

```toml
testpaths = ["tests", "users/tests.py", "municipalities/tests.py", "meetings/tests.py"]
```

**Step 2: Run full test suite**

```bash
uv run pytest -v
```

Expected: All tests pass with coverage >= 70%

**Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "chore: add notebooks to test coverage"
```

---

## Task 16: Final verification and cleanup

**Step 1: Run all checks**

```bash
uv run ruff check .
uv run mypy .
uv run pytest
```

**Step 2: Fix any issues found**

**Step 3: Create final commit if needed**

```bash
git add -A
git commit -m "chore: fix linting and type issues in notebooks"
```

**Step 4: Verify branch is ready**

```bash
git log --oneline -10
git status
```

---

## Summary

This implementation plan creates the complete notebooks feature:

1. **Models**: Notebook, Tag, NotebookEntry with proper constraints
2. **Views**: List, create, detail, edit, archive, delete for notebooks
3. **Entry management**: Edit notes/tags, delete entries
4. **Search integration**: Save button on search results with HTMX
5. **Tag system**: Create and manage tags per user
6. **Navigation**: Added notebooks to main navigation

All tasks follow TDD with failing tests first, then implementation.
