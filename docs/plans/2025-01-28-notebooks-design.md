# Notebooks Feature Design

## Overview

Notebooks allow logged-in users to save specific search results (MeetingPages) to collections for later reference. Users can annotate saved pages with notes and tags. The feature is designed with future team sharing in mind but launches as private-only.

## Data Model

### Notebook

| Field | Type | Description |
|-------|------|-------------|
| id | UUID | Primary key |
| user | FK to User | Owner of the notebook |
| name | CharField | User-defined title |
| is_archived | Boolean | Default False, hides from main list |
| created_at | DateTime | Auto-set on creation |
| updated_at | DateTime | Auto-updated on save |

### NotebookEntry

| Field | Type | Description |
|-------|------|-------------|
| id | UUID | Primary key |
| notebook | FK to Notebook | Parent notebook |
| meeting_page | FK to MeetingPage | The saved page |
| note | TextField | Optional user note (blank/null allowed) |
| created_at | DateTime | Auto-set on creation |
| updated_at | DateTime | Auto-updated on save |

**Constraint**: Unique together `(notebook, meeting_page)` - prevents duplicate saves.

### Tag

| Field | Type | Description |
|-------|------|-------------|
| id | UUID | Primary key |
| name | CharField | Tag text |
| user | FK to User | Creator (nullable for future team tags) |
| team | FK to Team | Future field (nullable) |

**Constraint**: Unique together `(name, user)` where team is null.

### NotebookEntryTag (M2M through table)

| Field | Type | Description |
|-------|------|-------------|
| entry | FK to NotebookEntry | The entry |
| tag | FK to Tag | The tag |

## User Flows

### Saving a page from search results

1. User performs a search, sees results (MeetingPages)
2. Each result has a save button/icon
3. Click saves page to most-recently-used notebook (or first notebook if none used)
4. Toast notification: "Saved to [Notebook Name]" with "Change" link
5. Clicking "Change" opens dropdown/modal to select different notebook or create new
6. If user has no notebooks, clicking Save prompts to create one first
7. Duplicate attempts show: "Already in [Notebook Name]"

### Creating a notebook

1. From notebooks list: "New Notebook" button with simple name field
2. From save flow (no notebooks exist): prompted with "Create your first notebook"

### Viewing notebooks list

1. Shows all active notebooks (archived hidden by default)
2. Each notebook shows: name, entry count, last updated
3. Toggle to show/hide archived notebooks
4. Click notebook to view details

### Viewing a notebook

1. Shows all saved pages in the notebook
2. Each entry shows: page snippet, document info, note (if any), tags (if any)
3. Options per entry: edit note/tags, remove from notebook
4. Notebook-level actions: edit name, archive/unarchive, delete

### Adding notes/tags to an entry

1. From notebook detail view, click "Edit" on an entry
2. Inline or modal form with note textarea and tag selector
3. Tag selector shows existing tags, allows creating new ones inline

## URL Structure

| URL | View | Purpose |
|-----|------|---------|
| `/notebooks/` | NotebookListView | List user's notebooks |
| `/notebooks/create/` | NotebookCreateView | Create new notebook |
| `/notebooks/<uuid>/` | NotebookDetailView | View notebook entries |
| `/notebooks/<uuid>/edit/` | NotebookEditView | Edit notebook name |
| `/notebooks/<uuid>/delete/` | NotebookDeleteView | Delete notebook |
| `/notebooks/<uuid>/archive/` | NotebookArchiveView | Toggle archive status |
| `/notebooks/<uuid>/entries/<uuid>/` | EntryEditView | Edit entry note/tags |
| `/notebooks/<uuid>/entries/<uuid>/delete/` | EntryDeleteView | Remove entry |
| `/notebooks/save-page/` | SavePageView | HTMX POST - save page to notebook |
| `/notebooks/tags/` | TagListView | HTMX GET - user's tags for autocomplete |
| `/notebooks/tags/create/` | TagCreateView | HTMX POST - create new tag |

All views require login. Users can only access their own notebooks.

## UI Components

### Search results save button

- Bookmark/save icon on each result row
- Unsaved: outline icon
- Saved (in any notebook): filled icon
- Click triggers HTMX POST, swaps state on success

### Toast notifications

- Bottom-center or bottom-right
- Auto-dismisses after 3-4 seconds
- Shows notebook name and "Change" link

### Notebooks list page

- Header with "Notebooks" title and "New Notebook" button
- Cards/rows: name, entry count, last updated
- Archive toggle
- Empty state with CTA

### Notebook detail page

- Header: notebook name, entry count
- Actions: Archive, Delete
- Entry list as cards:
  - Page text snippet (~150 chars)
  - Source: Municipality, Meeting Name, Date, Document Type
  - Note preview
  - Tag chips
  - Edit/Remove buttons
- Empty state message

### Entry edit form

- Note textarea
- Tag input with autocomplete
- Save/Cancel buttons

## Implementation

### New Django app: `notebooks`

**Models** (`notebooks/models.py`)
- Notebook, NotebookEntry, Tag models
- UUIDs for primary keys
- Standard timestamps

**Views** (`notebooks/views.py`)
- Class-based views with LoginRequiredMixin
- HTMX-friendly responses
- QuerySet filtering for user ownership

**Templates** (`templates/notebooks/`)
- `notebook_list.html`
- `notebook_detail.html`
- `notebook_form.html`
- `entry_form.html`
- `partials/` for HTMX fragments

**Search integration**
- Update search results template with save button
- Include saved-page context in search view

**Tests**
- Model constraint tests
- View CRUD and permission tests
- Integration tests for save-from-search flow

## Future Team Sharing

The design leaves room for team sharing without implementing it now.

### Tag model ready for teams

- `user` field is nullable
- `team` field exists (nullable)
- Team tags: `team` set, `user` null
- Unique constraint extensible to `(name, team)`

### Notebook extension path

When teams are added:
- Add `team` FK to Notebook (nullable)
- Add `visibility` field: "private" | "team"
- Add `NotebookPermission` model for roles (owner/editor/viewer)

### Migration path

1. Add new fields with nullable/defaults
2. Existing notebooks default to "private"
3. Existing tags remain user-level
4. New UI surfaces team options

### What we're NOT building now

- No Team model
- No visibility settings
- No permission checks beyond ownership
- No shared tag pools
