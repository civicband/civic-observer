# Production Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden CivicObserver so it can safely serve production traffic, by fixing configuration/secret handling, authorization, XSS, ingestion data integrity, and operational reliability in priority order.

**Architecture:** Five phases, each independently shippable and testable. Phase 1 fixes the production configuration surface (settings module wiring, secret-key handling, transport hardening, Sentry data minimization, env templates). Later phases fix authorization/XSS, ingestion integrity, job/queue reliability, and medium-risk hardening.

**Tech Stack:** Django 5.2, uvicorn/ASGI, PostgreSQL + psycopg3, Redis + django-rq, django-environs, django-anemometer/Sentry, Tailwind/WhiteNoise, Docker Compose blue-green deploy, pytest.

**Spec:** This document (derived from the production-readiness review of 2026-10-05).

## Global Constraints

- Python `>=3.13` (`pyproject.toml`), runtime image `python:3.13-slim-bookworm`.
- Never commit real secrets. `.env`, `.env.production` are gitignored; only `.env.example` / `.env-dist` are tracked.
- Follow existing settings style: `environs.env`, type annotations, and `# type: ignore[no-redef]` on overrides.
- Tests run with `uv run --group test pytest`; Docker `db` + `redis` must be running.
- Do not commit changes unless the user explicitly requests it.

## Review Focus

Behaviors most likely to bite a user that tests must pin:

- Production must fail to boot when `SECRET_KEY` is missing or the insecure default, rather than silently signing cookies with a known key.
- `/health/` must stay reachable over plain HTTP from inside the Docker network (healthcheck) even when `SECURE_SSL_REDIRECT` is on.
- Docker healthchecks and deploy verification must not be fooled by redirects; `/health/` is exempted from SSL redirect.
- Sentry must not receive request bodies or local variables that may contain credentials.
- CSRF trust must not include a plaintext `http://` production origin.

---

## Phase 1 — Production configuration & secret handling (execute now)

### Task 1.1: Require `SECRET_KEY` in production and reject the insecure default

**Files:**
- Modify: `config/settings/base.py:8-10`
- Modify: `config/settings/production.py:1-8,88`
- Test: `tests/settings/test_production.py`
- Test: `tests/settings/__init__.py` (exists)

**Interfaces:**
- Produces: `config.settings.base.INSECURE_SECRET_KEY: str`; `config.settings.production.SECRET_KEY: str`.

- [ ] **Step 1: Write the failing tests** in `tests/settings/test_production.py`
- [ ] **Step 2: Run tests, verify fail**
- [ ] **Step 3: Introduce `INSECURE_SECRET_KEY` sentinel in base; make production read `SECRET_KEY` with no default and raise `ImproperlyConfigured` if it equals the sentinel**
- [ ] **Step 4: Run tests, verify pass**
- [ ] **Step 5: Verify Django check still passes**

### Task 1.2: Enable production transport/session hardening

**Files:**
- Modify: `config/settings/production.py` (security block, `CSRF_TRUSTED_ORIGINS`)
- Test: `tests/settings/test_production.py`

- [ ] **Step 1: Add failing assertions** for `SECURE_SSL_REDIRECT`, `SECURE_REDIRECT_EXEMPT`, `SESSION_COOKIE_SECURE`, HSTS, `SECURE_PROXY_SSL_HEADER`, and no `http://` CSRF origin
- [ ] **Step 2: Run, verify fail**
- [ ] **Step 3: Set the values**
- [ ] **Step 4: Run, verify pass**

### Task 1.3: Minimize data sent to Sentry

**Files:**
- Modify: `config/settings/production.py` (sentry init + `before_send`)
- Test: `tests/settings/test_production.py`

- [ ] **Step 1: Add failing test** patching `sentry_sdk.init` and asserting `send_default_pii is False`, `max_request_body_size == "never"`, `include_local_variables is False`
- [ ] **Step 2: Run, verify fail**
- [ ] **Step 3: Change options and add header/body scrubbing in `before_send`**
- [ ] **Step 4: Run, verify pass**

### Task 1.4: Tracked env template + documentation correctness

**Files:**
- Create: `.env.example`
- Modify: `.env-dist`
- Modify: `deploy/README.md`
- Modify: `README.md`

- [ ] **Step 1: Create `.env.example` with all required vars (placeholders) standardizing on `SECRET_KEY` and `DJANGO_SETTINGS_MODULE=config.settings.production`**
- [ ] **Step 2: Align `.env-dist` on `SECRET_KEY`**
- [ ] **Step 3: Fix `deploy/README.md` env-file/`cp` instructions and compose filenames**
- [ ] **Step 4: Note the settings module / secret in `README.md`**

### Task 1.5: Correct local (gitignored) env files

**Files:**
- Modify: `.env`
- Modify: `.env.production`

- [ ] **Step 1: Rename `DJANGO_SECRET_KEY` → `SECRET_KEY` in both**
- [ ] **Step 2: Set `.env.production` `DJANGO_SETTINGS_MODULE=config.settings.production`**
- [ ] **Step 3: Confirm neither file is tracked by git**

---

## Phase 2 — Authorization & output escaping (outline)

- Gate municipal create/update/delete on `is_staff`/permission, not just `is_authenticated` (`municipalities/views.py:106-114`).
- Escape search snippet; stop using `|safe` over OCR text (`templates/meetings/partials/search_results.html:113`).
- Replace `innerHTML` DOM-XSS in `templates/meetings/meeting_search.html:590,677` with `textContent`/DOM APIs.
- Fix reflected XSS in `notebooks/views.py:187`.
- Pass municipality names via `json_script` instead of inline Alpine `x-data` (`templates/searches/partials/municipality_searchable_field.html:2`).
- Enforce public-page scope/search term in `meetings/views.py:90-176`.
- Constant-time secret comparisons; stop trusting `X-Forwarded-For` for the Tailscale check (`apikeys/internal_views.py:28-49`, `municipalities/views.py:139`).

## Phase 3 — Ingestion data integrity (outline)

- Re-raise HTTP errors and only mark backfills `completed` on normal termination (`meetings/services.py:179-186`, `meetings/tasks.py:236-353`).
- Make backfill status + enqueue atomic/idempotent; add per-job timeouts and RQ retry/failure callbacks.
- Reconnect immediate notifications to ingestion; move watermark off shared `Search` (`searches/models.py:173`).
- Failure-isolate digest sends; never send inside a DB transaction; guard with locking.

## Phase 4 — Operational reliability (outline)

- Queue-safe Redis eviction policy / separate Redis DB for RQ; add dead-letter alerting.
- Make `/health/` return 503 on DB error and check Redis (`config/views.py:15-20`).
- Run migrations once in deploy, not on every container start (`compose-entrypoint.sh`).
- Pin dependency install to `uv.lock` in the Docker build.
- Harden CI SSH host-key verification; make `safety` blocking or replace with `pip-audit`.
- Stream logs to stdout (Vector consumes container logs).

## Phase 5 — Medium hardening (outline)

- Versioned cache namespace including the count cache; replace Redis `KEYS`.
- Add uniqueness constraint for canonical `Search` params; make ingestion upserts conflict-safe.
- Sender timeouts, 429/Retry-After handling, atomic failure counters.
- Rate limiting on auth and webhooks; remove `http://` origins and broad exception disclosure.
- Remove/regenerate stale `requirements.txt`.
