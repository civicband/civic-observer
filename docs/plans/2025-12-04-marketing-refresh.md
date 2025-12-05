# Marketing Refresh Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Refresh the landing page with interactive feature demos using hardcoded JSON data, and add a new /api page for researchers and journalists.

**Architecture:** Replace the current landing page with a hero section followed by alternating feature blocks, each with a live interactive preview. Use hardcoded JSON data in templates for client-side interactivity (Alpine.js) to avoid DB queries from bot traffic. Add a new /api page with layered content (mission → use cases → technical → docs link).

**Tech Stack:** Django templates, Alpine.js for interactivity, Tailwind CSS, hardcoded JSON data

---

## Task 1: Update Landing Page Hero Section

**Files:**
- Modify: `templates/homepage.html`

**Step 1: Update the hero section**

Replace the current hero with a cleaner design that has the main value proposition and tiered CTAs:

```html
{% extends "base.html" %}
{% load static %}

{% block title %}{{ title }} - Civic Transparency Platform{% endblock %}

{% block content %}
<!-- Hero Section -->
<div class="relative overflow-hidden bg-gradient-to-br from-indigo-50 to-white">
    <div class="absolute inset-0 overflow-hidden" aria-hidden="true">
        <div class="absolute -top-40 -right-40 w-80 h-80 bg-indigo-100 rounded-full opacity-50 blur-3xl"></div>
        <div class="absolute -bottom-40 -left-40 w-80 h-80 bg-indigo-100 rounded-full opacity-50 blur-3xl"></div>
    </div>

    <div class="relative max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-24 sm:py-32">
        <div class="text-center max-w-3xl mx-auto">
            <h1 class="text-4xl sm:text-5xl lg:text-6xl font-extrabold text-gray-900 tracking-tight">
                Stay informed about<br>
                <span class="text-indigo-600">local government</span>
            </h1>
            <p class="mt-6 text-xl text-gray-600 max-w-2xl mx-auto">
                Search meeting agendas and minutes, save important pages, and get notified when topics you care about come up in your community.
            </p>
            <div class="mt-10 flex flex-col sm:flex-row gap-4 justify-center">
                <a href="{% url 'stagedoor:auth' %}" class="inline-flex items-center justify-center px-8 py-4 border border-transparent text-lg font-medium rounded-lg text-white bg-indigo-600 hover:bg-indigo-700 shadow-lg hover:shadow-xl transition-all duration-200">
                    Get Started Free
                    <svg class="ml-2 w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 7l5 5m0 0l-5 5m5-5H6"></path>
                    </svg>
                </a>
                <a href="#features" class="inline-flex items-center justify-center px-8 py-4 border border-gray-300 text-lg font-medium rounded-lg text-gray-700 bg-white hover:bg-gray-50 shadow hover:shadow-md transition-all duration-200">
                    See how it works
                </a>
            </div>
        </div>
    </div>
</div>
```

**Step 2: Verify the template renders**

Run the dev server and check http://localhost:8000/

**Step 3: Commit**

```bash
git add templates/homepage.html
git commit -m "feat(marketing): update hero section with cleaner design and tiered CTAs"
```

---

## Task 2: Add Search Feature Block with Demo

**Files:**
- Modify: `templates/homepage.html`

**Step 1: Add the search feature block after the hero**

Add an alternating feature block with a hardcoded search demo:

```html
<!-- Feature 1: Search -->
<div id="features" class="py-24 bg-white">
    <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div class="lg:grid lg:grid-cols-2 lg:gap-16 items-center">
            <!-- Text Content -->
            <div>
                <div class="inline-flex items-center px-3 py-1 rounded-full text-sm font-medium bg-indigo-100 text-indigo-800 mb-4">
                    <svg class="w-4 h-4 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"></path>
                    </svg>
                    Search
                </div>
                <h2 class="text-3xl font-bold text-gray-900 sm:text-4xl">
                    Find what matters in meeting documents
                </h2>
                <p class="mt-4 text-lg text-gray-600">
                    Search across thousands of meeting agendas and minutes from municipalities across the country. Use powerful filters to narrow down by date, location, or document type.
                </p>
                <div class="mt-8">
                    <a href="{% url 'meetings:meeting-search' %}" class="inline-flex items-center text-indigo-600 font-medium hover:text-indigo-700">
                        Try searching now
                        <svg class="ml-2 w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 7l5 5m0 0l-5 5m5-5H6"></path>
                        </svg>
                    </a>
                </div>
            </div>

            <!-- Interactive Demo -->
            <div class="mt-12 lg:mt-0" x-data="searchDemo()">
                <div class="bg-gray-50 rounded-2xl p-6 shadow-lg border border-gray-200">
                    <div class="flex items-center gap-3 mb-4">
                        <div class="flex-1">
                            <input type="text"
                                   x-model="query"
                                   @input="filterResults()"
                                   placeholder="Try searching: budget, housing, zoning..."
                                   class="w-full px-4 py-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500">
                        </div>
                    </div>

                    <div class="space-y-3 max-h-64 overflow-y-auto">
                        <template x-for="result in filteredResults" :key="result.id">
                            <div class="bg-white p-4 rounded-lg border border-gray-200 hover:border-indigo-300 transition-colors">
                                <div class="flex items-start justify-between">
                                    <div class="flex-1 min-w-0">
                                        <p class="text-sm font-medium text-gray-900" x-text="result.title"></p>
                                        <p class="text-xs text-gray-500 mt-1">
                                            <span x-text="result.municipality"></span> • <span x-text="result.date"></span>
                                        </p>
                                        <p class="text-sm text-gray-600 mt-2 line-clamp-2" x-text="result.snippet"></p>
                                    </div>
                                    <span class="ml-3 inline-flex items-center px-2 py-1 rounded text-xs font-medium"
                                          :class="result.type === 'agenda' ? 'bg-blue-100 text-blue-800' : 'bg-green-100 text-green-800'"
                                          x-text="result.type"></span>
                                </div>
                            </div>
                        </template>
                        <div x-show="filteredResults.length === 0" class="text-center py-8 text-gray-500">
                            <p>No results found. Try a different search term.</p>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>
</div>
```

**Step 2: Add the Alpine.js searchDemo component at the bottom of the template**

```html
<script>
function searchDemo() {
    return {
        query: '',
        results: [
            {
                id: 1,
                title: 'City Council Regular Meeting - Budget Discussion',
                municipality: 'Berkeley, CA',
                date: 'Nov 15, 2024',
                type: 'agenda',
                snippet: 'Discussion of FY2025 budget allocations including housing assistance programs and infrastructure improvements...'
            },
            {
                id: 2,
                title: 'Planning Commission - Zoning Amendment Review',
                municipality: 'Oakland, CA',
                date: 'Nov 12, 2024',
                type: 'minutes',
                snippet: 'Review of proposed zoning changes for mixed-use development in the downtown corridor...'
            },
            {
                id: 3,
                title: 'Housing Authority Board Meeting',
                municipality: 'San Francisco, CA',
                date: 'Nov 10, 2024',
                type: 'agenda',
                snippet: 'Approval of affordable housing initiatives and tenant assistance program updates...'
            },
            {
                id: 4,
                title: 'Budget Committee - Q3 Financial Review',
                municipality: 'Alameda, CA',
                date: 'Nov 8, 2024',
                type: 'minutes',
                snippet: 'Review of third quarter expenditures and revenue projections for municipal services...'
            },
            {
                id: 5,
                title: 'City Council - Housing Policy Update',
                municipality: 'Fremont, CA',
                date: 'Nov 5, 2024',
                type: 'agenda',
                snippet: 'First reading of updated housing policy including density bonus provisions...'
            }
        ],
        filteredResults: [],

        init() {
            this.filteredResults = this.results;
        },

        filterResults() {
            if (!this.query.trim()) {
                this.filteredResults = this.results;
                return;
            }
            const q = this.query.toLowerCase();
            this.filteredResults = this.results.filter(r =>
                r.title.toLowerCase().includes(q) ||
                r.snippet.toLowerCase().includes(q) ||
                r.municipality.toLowerCase().includes(q)
            );
        }
    };
}
</script>
```

**Step 3: Verify the search demo works**

Run the dev server and test the interactive search on the homepage.

**Step 4: Commit**

```bash
git add templates/homepage.html
git commit -m "feat(marketing): add interactive search demo feature block"
```

---

## Task 3: Add Notebook Feature Block with Demo

**Files:**
- Modify: `templates/homepage.html`

**Step 1: Add the notebook feature block (right-aligned text, left demo)**

```html
<!-- Feature 2: Notebooks -->
<div class="py-24 bg-gray-50">
    <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div class="lg:grid lg:grid-cols-2 lg:gap-16 items-center">
            <!-- Interactive Demo (Left side) -->
            <div class="order-2 lg:order-1 mt-12 lg:mt-0" x-data="notebookDemo()">
                <div class="bg-white rounded-2xl p-6 shadow-lg border border-gray-200">
                    <div class="flex items-center justify-between mb-4">
                        <h3 class="font-semibold text-gray-900">My Research Notebook</h3>
                        <span class="text-sm text-gray-500" x-text="savedPages.length + ' pages'"></span>
                    </div>

                    <div class="space-y-3 max-h-64 overflow-y-auto">
                        <template x-for="page in savedPages" :key="page.id">
                            <div class="p-4 rounded-lg border border-gray-200 hover:border-indigo-300 transition-colors">
                                <div class="flex items-start justify-between">
                                    <div class="flex-1 min-w-0">
                                        <p class="text-sm font-medium text-gray-900" x-text="page.title"></p>
                                        <p class="text-xs text-gray-500 mt-1" x-text="page.source"></p>
                                    </div>
                                    <button @click="removePage(page.id)"
                                            class="ml-2 text-gray-400 hover:text-red-500 transition-colors">
                                        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path>
                                        </svg>
                                    </button>
                                </div>
                                <div class="mt-2 flex flex-wrap gap-1">
                                    <template x-for="tag in page.tags" :key="tag">
                                        <span class="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-gray-100 text-gray-700" x-text="tag"></span>
                                    </template>
                                </div>
                            </div>
                        </template>
                    </div>

                    <div class="mt-4 pt-4 border-t border-gray-200">
                        <button @click="addSamplePage()"
                                class="w-full inline-flex items-center justify-center px-4 py-2 border border-dashed border-gray-300 rounded-lg text-sm text-gray-600 hover:border-indigo-500 hover:text-indigo-600 transition-colors">
                            <svg class="w-4 h-4 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 6v6m0 0v6m0-6h6m-6 0H6"></path>
                            </svg>
                            Save another page
                        </button>
                    </div>
                </div>
            </div>

            <!-- Text Content (Right side) -->
            <div class="order-1 lg:order-2">
                <div class="inline-flex items-center px-3 py-1 rounded-full text-sm font-medium bg-indigo-100 text-indigo-800 mb-4">
                    <svg class="w-4 h-4 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253"></path>
                    </svg>
                    Notebooks
                </div>
                <h2 class="text-3xl font-bold text-gray-900 sm:text-4xl">
                    Save and organize your research
                </h2>
                <p class="mt-4 text-lg text-gray-600">
                    Clip important pages from meeting documents directly to your notebooks. Tag them, organize them, and access them anytime. Perfect for journalists, researchers, and engaged citizens.
                </p>
                <div class="mt-8">
                    <a href="{% url 'notebooks:notebook-list' %}" class="inline-flex items-center text-indigo-600 font-medium hover:text-indigo-700">
                        Create your first notebook
                        <svg class="ml-2 w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 7l5 5m0 0l-5 5m5-5H6"></path>
                        </svg>
                    </a>
                </div>
            </div>
        </div>
    </div>
</div>
```

**Step 2: Add the notebookDemo Alpine component**

```javascript
function notebookDemo() {
    return {
        savedPages: [
            {
                id: 1,
                title: 'Budget Discussion - FY2025 Allocations',
                source: 'Berkeley City Council • Nov 15, 2024',
                tags: ['budget', 'housing']
            },
            {
                id: 2,
                title: 'Zoning Amendment Review',
                source: 'Oakland Planning Commission • Nov 12, 2024',
                tags: ['zoning', 'development']
            },
            {
                id: 3,
                title: 'Affordable Housing Initiatives',
                source: 'SF Housing Authority • Nov 10, 2024',
                tags: ['housing', 'affordable']
            }
        ],
        nextId: 4,
        samplePages: [
            { title: 'Transit Funding Discussion', source: 'BART Board • Nov 1, 2024', tags: ['transit', 'budget'] },
            { title: 'Environmental Impact Review', source: 'County Planning • Oct 28, 2024', tags: ['environment'] },
            { title: 'Public Safety Committee Report', source: 'City Council • Oct 25, 2024', tags: ['safety'] }
        ],

        removePage(id) {
            this.savedPages = this.savedPages.filter(p => p.id !== id);
        },

        addSamplePage() {
            if (this.samplePages.length === 0) return;
            const page = this.samplePages.shift();
            this.savedPages.push({
                id: this.nextId++,
                ...page
            });
        }
    };
}
```

**Step 3: Verify the notebook demo works**

Test adding/removing pages in the demo.

**Step 4: Commit**

```bash
git add templates/homepage.html
git commit -m "feat(marketing): add interactive notebook demo feature block"
```

---

## Task 4: Add Saved Search Feature Block with Demo

**Files:**
- Modify: `templates/homepage.html`

**Step 1: Add the saved search feature block (left text, right demo)**

```html
<!-- Feature 3: Saved Searches -->
<div class="py-24 bg-white">
    <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div class="lg:grid lg:grid-cols-2 lg:gap-16 items-center">
            <!-- Text Content -->
            <div>
                <div class="inline-flex items-center px-3 py-1 rounded-full text-sm font-medium bg-indigo-100 text-indigo-800 mb-4">
                    <svg class="w-4 h-4 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 5a2 2 0 012-2h10a2 2 0 012 2v16l-7-3.5L5 21V5z"></path>
                    </svg>
                    Saved Searches
                </div>
                <h2 class="text-3xl font-bold text-gray-900 sm:text-4xl">
                    Never miss an important topic
                </h2>
                <p class="mt-4 text-lg text-gray-600">
                    Save your search criteria and we'll monitor new meeting documents for you. Get notified the moment something relevant is published.
                </p>
                <div class="mt-8">
                    <a href="{% url 'searches:savedsearch-list' %}" class="inline-flex items-center text-indigo-600 font-medium hover:text-indigo-700">
                        Set up your first saved search
                        <svg class="ml-2 w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 7l5 5m0 0l-5 5m5-5H6"></path>
                        </svg>
                    </a>
                </div>
            </div>

            <!-- Interactive Demo -->
            <div class="mt-12 lg:mt-0" x-data="savedSearchDemo()">
                <div class="bg-gray-50 rounded-2xl p-6 shadow-lg border border-gray-200">
                    <h3 class="font-semibold text-gray-900 mb-4">Your Saved Searches</h3>

                    <div class="space-y-3">
                        <template x-for="search in searches" :key="search.id">
                            <div class="bg-white p-4 rounded-lg border border-gray-200">
                                <div class="flex items-center justify-between">
                                    <div>
                                        <p class="font-medium text-gray-900" x-text="search.name"></p>
                                        <p class="text-sm text-gray-500 mt-1">
                                            <span class="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-indigo-100 text-indigo-800" x-text="'\"' + search.term + '\"'"></span>
                                            <span class="ml-2" x-text="search.scope"></span>
                                        </p>
                                    </div>
                                    <div class="flex items-center gap-2">
                                        <span x-show="search.newResults > 0"
                                              class="inline-flex items-center px-2 py-1 rounded-full text-xs font-medium bg-yellow-100 text-yellow-800">
                                            <span x-text="search.newResults"></span> new
                                        </span>
                                        <span class="inline-flex items-center px-2 py-1 rounded text-xs font-medium"
                                              :class="search.frequency === 'immediate' ? 'bg-red-100 text-red-800' : search.frequency === 'daily' ? 'bg-blue-100 text-blue-800' : 'bg-green-100 text-green-800'"
                                              x-text="search.frequency"></span>
                                    </div>
                                </div>
                            </div>
                        </template>
                    </div>

                    <div class="mt-4 p-4 bg-indigo-50 rounded-lg border border-indigo-100">
                        <div class="flex items-start">
                            <svg class="w-5 h-5 text-indigo-600 mt-0.5 mr-3 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path>
                            </svg>
                            <p class="text-sm text-indigo-800">
                                <strong>Tip:</strong> Use "immediate" for time-sensitive topics and "weekly" for general monitoring.
                            </p>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>
</div>
```

**Step 2: Add the savedSearchDemo Alpine component**

```javascript
function savedSearchDemo() {
    return {
        searches: [
            {
                id: 1,
                name: 'Housing Policy Updates',
                term: 'affordable housing',
                scope: '3 municipalities',
                frequency: 'immediate',
                newResults: 2
            },
            {
                id: 2,
                name: 'Budget Discussions',
                term: 'budget',
                scope: 'All Bay Area',
                frequency: 'daily',
                newResults: 5
            },
            {
                id: 3,
                name: 'Zoning Changes',
                term: 'zoning amendment',
                scope: 'Oakland, CA',
                frequency: 'weekly',
                newResults: 0
            }
        ]
    };
}
```

**Step 3: Verify the saved search demo displays correctly**

Check that the frequency badges and new result indicators work.

**Step 4: Commit**

```bash
git add templates/homepage.html
git commit -m "feat(marketing): add saved search demo feature block"
```

---

## Task 5: Add Static Notifications Feature Block

**Files:**
- Modify: `templates/homepage.html`

**Step 1: Add the notifications feature block (right text, left static preview)**

```html
<!-- Feature 4: Notifications (Static) -->
<div class="py-24 bg-gray-50">
    <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div class="lg:grid lg:grid-cols-2 lg:gap-16 items-center">
            <!-- Static Preview (Left side) -->
            <div class="order-2 lg:order-1 mt-12 lg:mt-0">
                <div class="bg-white rounded-2xl p-6 shadow-lg border border-gray-200">
                    <h3 class="font-semibold text-gray-900 mb-4">Notification Channels</h3>

                    <div class="space-y-4">
                        <!-- Email Channel -->
                        <div class="flex items-center justify-between p-4 rounded-lg border border-gray-200">
                            <div class="flex items-center">
                                <div class="w-10 h-10 rounded-full bg-blue-100 flex items-center justify-center mr-4">
                                    <svg class="w-5 h-5 text-blue-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"></path>
                                    </svg>
                                </div>
                                <div>
                                    <p class="font-medium text-gray-900">Email</p>
                                    <p class="text-sm text-gray-500">researcher@example.com</p>
                                </div>
                            </div>
                            <span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-800">Active</span>
                        </div>

                        <!-- Bluesky Channel -->
                        <div class="flex items-center justify-between p-4 rounded-lg border border-gray-200">
                            <div class="flex items-center">
                                <div class="w-10 h-10 rounded-full bg-sky-100 flex items-center justify-center mr-4">
                                    <svg class="w-5 h-5 text-sky-600" viewBox="0 0 24 24" fill="currentColor">
                                        <path d="M12 2C6.477 2 2 6.477 2 12c0 4.991 3.657 9.128 8.438 9.879V14.89h-2.54V12h2.54V9.797c0-2.506 1.492-3.89 3.777-3.89 1.094 0 2.238.195 2.238.195v2.46h-1.26c-1.243 0-1.63.771-1.63 1.562V12h2.773l-.443 2.89h-2.33v6.989C18.343 21.129 22 16.99 22 12c0-5.523-4.477-10-10-10z"/>
                                    </svg>
                                </div>
                                <div>
                                    <p class="font-medium text-gray-900">Bluesky</p>
                                    <p class="text-sm text-gray-500">@researcher.bsky.social</p>
                                </div>
                            </div>
                            <span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-800">Active</span>
                        </div>

                        <!-- Webhook Channel -->
                        <div class="flex items-center justify-between p-4 rounded-lg border border-dashed border-gray-300 bg-gray-50">
                            <div class="flex items-center">
                                <div class="w-10 h-10 rounded-full bg-gray-200 flex items-center justify-center mr-4">
                                    <svg class="w-5 h-5 text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 20l4-16m4 4l4 4-4 4M6 16l-4-4 4-4"></path>
                                    </svg>
                                </div>
                                <div>
                                    <p class="font-medium text-gray-500">Webhook</p>
                                    <p class="text-sm text-gray-400">For developers & integrations</p>
                                </div>
                            </div>
                            <span class="text-sm text-gray-500">Coming soon</span>
                        </div>
                    </div>
                </div>
            </div>

            <!-- Text Content (Right side) -->
            <div class="order-1 lg:order-2">
                <div class="inline-flex items-center px-3 py-1 rounded-full text-sm font-medium bg-indigo-100 text-indigo-800 mb-4">
                    <svg class="w-4 h-4 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9"></path>
                    </svg>
                    Notifications
                </div>
                <h2 class="text-3xl font-bold text-gray-900 sm:text-4xl">
                    Get alerted your way
                </h2>
                <p class="mt-4 text-lg text-gray-600">
                    Choose how you want to receive updates. Get instant email notifications, post to Bluesky, or integrate with your own systems via webhooks.
                </p>
                <div class="mt-8">
                    <a href="{% url 'notifications:channel-list' %}" class="inline-flex items-center text-indigo-600 font-medium hover:text-indigo-700">
                        Configure your notifications
                        <svg class="ml-2 w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 7l5 5m0 0l-5 5m5-5H6"></path>
                        </svg>
                    </a>
                </div>
            </div>
        </div>
    </div>
</div>
```

**Step 2: Commit**

```bash
git add templates/homepage.html
git commit -m "feat(marketing): add static notifications feature block"
```

---

## Task 6: Add Final CTA Section and Update Footer Link

**Files:**
- Modify: `templates/homepage.html`

**Step 1: Add the final CTA section before endblock**

```html
<!-- Final CTA -->
<div class="bg-indigo-600">
    <div class="max-w-7xl mx-auto py-16 px-4 sm:px-6 lg:py-24 lg:px-8 lg:flex lg:items-center lg:justify-between">
        <div>
            <h2 class="text-3xl font-extrabold tracking-tight text-white sm:text-4xl">
                Ready to stay informed?
            </h2>
            <p class="mt-4 text-lg text-indigo-100 max-w-xl">
                Join researchers, journalists, and engaged citizens who use CivicObserver to track local government.
            </p>
        </div>
        <div class="mt-8 flex flex-col sm:flex-row gap-4 lg:mt-0 lg:flex-shrink-0">
            <a href="{% url 'stagedoor:auth' %}" class="inline-flex items-center justify-center px-6 py-4 border border-transparent text-lg font-medium rounded-lg text-indigo-600 bg-white hover:bg-indigo-50 shadow-lg transition-all duration-200">
                Get Started Free
            </a>
            <a href="{% url 'meetings:meeting-search' %}" class="inline-flex items-center justify-center px-6 py-4 border-2 border-white text-lg font-medium rounded-lg text-white hover:bg-indigo-500 transition-all duration-200">
                Try Search First
            </a>
        </div>
    </div>
</div>

{% endblock content %}
```

**Step 2: Remove the old features section and CTA from homepage.html**

Delete the old `#features` section (icon grid) and old CTA section.

**Step 3: Verify the complete landing page**

Check that all sections flow correctly and demos work.

**Step 4: Commit**

```bash
git add templates/homepage.html
git commit -m "feat(marketing): add final CTA section and complete landing page refresh"
```

---

## Task 7: Create API Page View and URL

**Files:**
- Modify: `config/views.py`
- Modify: `config/urls.py`

**Step 1: Add the api_page view to config/views.py**

```python
def api_page(request):
    """API information page for researchers and developers."""
    return render(request, "api.html")
```

**Step 2: Add the URL pattern to config/urls.py**

Add to urlpatterns:
```python
path("api/", views.api_page, name="api_page"),
```

**Step 3: Commit**

```bash
git add config/views.py config/urls.py
git commit -m "feat(api): add api page view and URL route"
```

---

## Task 8: Create API Page Template

**Files:**
- Create: `templates/api.html`

**Step 1: Create the API page template**

```html
{% extends "base.html" %}

{% block title %}API Access - CivicObserver{% endblock %}

{% block content %}
<!-- Hero Section -->
<div class="bg-gradient-to-br from-gray-900 to-indigo-900 text-white">
    <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-24">
        <div class="max-w-3xl">
            <h1 class="text-4xl sm:text-5xl font-extrabold tracking-tight">
                Access civic data<br>programmatically
            </h1>
            <p class="mt-6 text-xl text-gray-300">
                CivicObserver provides API access to meeting documents, search results, and notification data for researchers, journalists, and civic tech developers.
            </p>
            <div class="mt-10 flex flex-col sm:flex-row gap-4">
                <a href="{% url 'apikeys:list' %}" class="inline-flex items-center justify-center px-6 py-3 border border-transparent text-base font-medium rounded-lg text-indigo-900 bg-white hover:bg-gray-100 transition-colors">
                    Get Your API Key
                </a>
                <a href="https://docs.civic.band" target="_blank" rel="noopener" class="inline-flex items-center justify-center px-6 py-3 border border-white text-base font-medium rounded-lg text-white hover:bg-white/10 transition-colors">
                    View Documentation
                    <svg class="ml-2 w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"></path>
                    </svg>
                </a>
            </div>
        </div>
    </div>
</div>

<!-- Mission Section -->
<div class="py-24 bg-white">
    <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div class="max-w-3xl mx-auto text-center">
            <h2 class="text-3xl font-bold text-gray-900">Built for transparency</h2>
            <p class="mt-6 text-lg text-gray-600">
                Local government decisions affect everyone. CivicObserver makes it easier to access, search, and monitor public meeting documents. Our API extends this mission by enabling researchers, journalists, and developers to build tools that further civic engagement.
            </p>
        </div>
    </div>
</div>

<!-- Use Cases Section -->
<div class="py-24 bg-gray-50">
    <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <h2 class="text-3xl font-bold text-gray-900 text-center mb-16">Who uses the API?</h2>

        <div class="grid md:grid-cols-2 gap-8">
            <!-- Researchers & Journalists -->
            <div class="bg-white rounded-xl p-8 shadow-sm border border-gray-200">
                <div class="w-12 h-12 rounded-lg bg-indigo-100 flex items-center justify-center mb-6">
                    <svg class="w-6 h-6 text-indigo-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"></path>
                    </svg>
                </div>
                <h3 class="text-xl font-semibold text-gray-900 mb-3">Researchers & Journalists</h3>
                <p class="text-gray-600 mb-4">
                    Analyze patterns in local government decision-making. Track how topics like housing, budget, or zoning evolve across municipalities over time.
                </p>
                <ul class="space-y-2 text-sm text-gray-600">
                    <li class="flex items-start">
                        <svg class="w-5 h-5 text-green-500 mr-2 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"></path>
                        </svg>
                        Bulk export search results for analysis
                    </li>
                    <li class="flex items-start">
                        <svg class="w-5 h-5 text-green-500 mr-2 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"></path>
                        </svg>
                        Set up programmatic alerts for breaking topics
                    </li>
                    <li class="flex items-start">
                        <svg class="w-5 h-5 text-green-500 mr-2 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"></path>
                        </svg>
                        Access historical meeting data
                    </li>
                </ul>
            </div>

            <!-- Civic Tech Developers -->
            <div class="bg-white rounded-xl p-8 shadow-sm border border-gray-200">
                <div class="w-12 h-12 rounded-lg bg-indigo-100 flex items-center justify-center mb-6">
                    <svg class="w-6 h-6 text-indigo-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 20l4-16m4 4l4 4-4 4M6 16l-4-4 4-4"></path>
                    </svg>
                </div>
                <h3 class="text-xl font-semibold text-gray-900 mb-3">Civic Tech Developers</h3>
                <p class="text-gray-600 mb-4">
                    Build applications that help communities engage with local government. Create dashboards, notification bots, or analysis tools.
                </p>
                <ul class="space-y-2 text-sm text-gray-600">
                    <li class="flex items-start">
                        <svg class="w-5 h-5 text-green-500 mr-2 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"></path>
                        </svg>
                        RESTful API with JSON responses
                    </li>
                    <li class="flex items-start">
                        <svg class="w-5 h-5 text-green-500 mr-2 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"></path>
                        </svg>
                        Webhook notifications for real-time updates
                    </li>
                    <li class="flex items-start">
                        <svg class="w-5 h-5 text-green-500 mr-2 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"></path>
                        </svg>
                        Rate limits suitable for most applications
                    </li>
                </ul>
            </div>
        </div>
    </div>
</div>

<!-- Getting Started Section -->
<div class="py-24 bg-white">
    <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <h2 class="text-3xl font-bold text-gray-900 text-center mb-16">Getting started</h2>

        <div class="max-w-3xl mx-auto">
            <div class="space-y-8">
                <!-- Step 1 -->
                <div class="flex gap-6">
                    <div class="flex-shrink-0 w-10 h-10 rounded-full bg-indigo-600 text-white flex items-center justify-center font-bold">1</div>
                    <div>
                        <h3 class="text-lg font-semibold text-gray-900">Create an account</h3>
                        <p class="mt-2 text-gray-600">Sign up for free to get access to the API. No credit card required.</p>
                        <a href="{% url 'stagedoor:auth' %}" class="mt-3 inline-flex items-center text-indigo-600 font-medium hover:text-indigo-700">
                            Sign up now
                            <svg class="ml-1 w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 7l5 5m0 0l-5 5m5-5H6"></path>
                            </svg>
                        </a>
                    </div>
                </div>

                <!-- Step 2 -->
                <div class="flex gap-6">
                    <div class="flex-shrink-0 w-10 h-10 rounded-full bg-indigo-600 text-white flex items-center justify-center font-bold">2</div>
                    <div>
                        <h3 class="text-lg font-semibold text-gray-900">Generate an API key</h3>
                        <p class="mt-2 text-gray-600">Create an API key from your account dashboard. You can create multiple keys for different applications.</p>
                        <a href="{% url 'apikeys:list' %}" class="mt-3 inline-flex items-center text-indigo-600 font-medium hover:text-indigo-700">
                            Manage API keys
                            <svg class="ml-1 w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 7l5 5m0 0l-5 5m5-5H6"></path>
                            </svg>
                        </a>
                    </div>
                </div>

                <!-- Step 3 -->
                <div class="flex gap-6">
                    <div class="flex-shrink-0 w-10 h-10 rounded-full bg-indigo-600 text-white flex items-center justify-center font-bold">3</div>
                    <div>
                        <h3 class="text-lg font-semibold text-gray-900">Start making requests</h3>
                        <p class="mt-2 text-gray-600">Use your API key to authenticate requests. Check our documentation for endpoints and examples.</p>
                        <a href="https://docs.civic.band" target="_blank" rel="noopener" class="mt-3 inline-flex items-center text-indigo-600 font-medium hover:text-indigo-700">
                            Read the docs
                            <svg class="ml-1 w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"></path>
                            </svg>
                        </a>
                    </div>
                </div>
            </div>

            <!-- Code Example -->
            <div class="mt-16">
                <h3 class="text-lg font-semibold text-gray-900 mb-4">Example request</h3>
                <div class="bg-gray-900 rounded-lg p-6 overflow-x-auto">
                    <pre class="text-sm text-gray-300"><code>curl -H "Authorization: Bearer YOUR_API_KEY" \
     "https://civic.observer/api/v1/search/?query=housing&municipality=berkeley"</code></pre>
                </div>
            </div>
        </div>
    </div>
</div>

<!-- CTA Section -->
<div class="bg-indigo-600">
    <div class="max-w-7xl mx-auto py-16 px-4 sm:px-6 lg:py-20 lg:px-8 text-center">
        <h2 class="text-3xl font-extrabold text-white">Ready to get started?</h2>
        <p class="mt-4 text-lg text-indigo-100 max-w-xl mx-auto">
            Create a free account and start exploring the API today.
        </p>
        <div class="mt-8 flex flex-col sm:flex-row gap-4 justify-center">
            <a href="{% url 'stagedoor:auth' %}" class="inline-flex items-center justify-center px-6 py-3 border border-transparent text-base font-medium rounded-lg text-indigo-600 bg-white hover:bg-gray-100 transition-colors">
                Create Free Account
            </a>
            <a href="https://docs.civic.band" target="_blank" rel="noopener" class="inline-flex items-center justify-center px-6 py-3 border-2 border-white text-base font-medium rounded-lg text-white hover:bg-indigo-500 transition-colors">
                Browse Documentation
            </a>
        </div>
    </div>
</div>
{% endblock content %}
```

**Step 2: Verify the API page renders correctly**

Visit http://localhost:8000/api/ and check all sections.

**Step 3: Commit**

```bash
git add templates/api.html
git commit -m "feat(api): create API information page for researchers and developers"
```

---

## Task 9: Add API Link to Navigation

**Files:**
- Modify: `templates/base.html`

**Step 1: Add API link to the desktop navigation**

In the desktop nav section (around line 71), add after the notifications link for authenticated users:

```html
<a href="{% url 'api_page' %}" class="text-gray-600 hover:text-indigo-600 font-medium transition-colors" data-umami-event="nav_click" data-umami-event-destination="api">API</a>
```

**Step 2: Add API link to the mobile navigation**

In the mobile menu section (around line 123), add:

```html
<a href="{% url 'api_page' %}" class="block px-3 py-2 rounded-md text-base font-medium text-gray-600 hover:text-indigo-600 hover:bg-gray-50 transition-colors" data-umami-event="nav_click" data-umami-event-destination="api">API</a>
```

**Step 3: Add API link to the footer**

In the footer Platform section, add:

```html
<li>
    <a href="{% url 'api_page' %}" class="text-base text-gray-400 hover:text-white transition-colors duration-200">
        API Access
    </a>
</li>
```

**Step 4: Verify navigation links work**

Check desktop and mobile navigation, and footer.

**Step 5: Commit**

```bash
git add templates/base.html
git commit -m "feat(marketing): add API page link to navigation and footer"
```

---

## Task 10: Clean Up and Final Testing

**Step 1: Run linting and type checking**

```bash
uv run --group dev ruff check .
uv run --group dev mypy .
```

**Step 2: Fix any issues found**

**Step 3: Test all pages manually**

- Homepage: Check all feature demos work
- API page: Check all sections and links
- Navigation: Check desktop and mobile
- Footer: Check API link

**Step 4: Final commit if any cleanup needed**

```bash
git add -A
git commit -m "chore(marketing): cleanup and formatting fixes"
```

---

## Summary

This plan creates:
1. **Refreshed landing page** with hero + 4 alternating feature blocks
2. **Interactive demos** using hardcoded JSON data (Search, Notebooks, Saved Searches)
3. **Static demo** for Notifications feature
4. **New /api page** with layered content for researchers/journalists
5. **Updated navigation** with API link in header and footer

All demos use client-side Alpine.js with hardcoded data, avoiding any database queries from bot traffic.
