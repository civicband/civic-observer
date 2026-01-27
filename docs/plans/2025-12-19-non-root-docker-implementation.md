# Non-Root Docker Container Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Configure Docker containers to run as non-root user (`app` with UID 1000) for improved security.

**Architecture:** Restructure Dockerfile into builder, asset-builder, and release stages. Build static assets at image time. Create `app` user in final stage with UID/GID 1000.

**Tech Stack:** Docker, Docker Compose, Django, Tailwind CSS

---

## Task 1: Restructure Dockerfile with Non-Root User

**Files:**
- Modify: `Dockerfile`

**Step 1: Read current Dockerfile**

Read the existing Dockerfile to understand current structure.

**Step 2: Rewrite Dockerfile with three stages**

Replace entire Dockerfile content with:

```dockerfile
# ------------------------------------------------------------
# Stage 1: Builder - Install dependencies
# ------------------------------------------------------------

FROM python:3.13-slim-bookworm AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY pyproject.toml /tmp/pyproject.toml

RUN --mount=type=cache,target=/root/.cache,sharing=locked,id=pip \
    python -m pip install --upgrade pip uv

RUN --mount=type=cache,target=/root/.cache,sharing=locked,id=pip \
    python -m uv pip compile /tmp/pyproject.toml -o /tmp/requirements.txt

RUN --mount=type=cache,target=/root/.cache,sharing=locked,id=pip \
    python -m uv pip install --system --requirement /tmp/requirements.txt

# ------------------------------------------------------------
# Stage 2: Asset Builder - Build static assets
# ------------------------------------------------------------

FROM builder AS asset-builder

# Install Node.js for Tailwind
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

COPY . /src/
WORKDIR /src/

# Build Tailwind CSS and collect static files
RUN python manage.py tailwind build
RUN python manage.py collectstatic --noinput --skip-checks

# ------------------------------------------------------------
# Stage 3: Release - Final production image
# ------------------------------------------------------------

FROM python:3.13-slim-bookworm AS release

ENV PIP_DISABLE_PIP_VERSION_CHECK=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH=/src
ENV PYTHONUNBUFFERED=1

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user with UID/GID 1000
RUN groupadd --gid 1000 app && \
    useradd --uid 1000 --gid 1000 --create-home --shell /bin/bash app

# Copy Python packages from builder
COPY --from=builder /usr/local/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application code
COPY --chown=app:app . /src/

# Copy pre-built static files from asset-builder
COPY --from=asset-builder --chown=app:app /src/staticfiles /src/staticfiles
COPY --from=asset-builder --chown=app:app /src/theme/static /src/theme/static

WORKDIR /src/

# Switch to non-root user
USER app

CMD ["uvicorn", "config.asgi:application", "--host", "0.0.0.0", "--limit-max-requests", "100", "--timeout-graceful-shutdown", "7"]
```

**Step 3: Commit Dockerfile changes**

```bash
git add Dockerfile
git commit -m "feat(docker): restructure Dockerfile for non-root execution

- Add three-stage build: builder, asset-builder, release
- Create app user with UID/GID 1000
- Build Tailwind and collectstatic at image build time
- Run final container as non-root user"
```

---

## Task 2: Update Entrypoint Script

**Files:**
- Modify: `compose-entrypoint.sh`

**Step 1: Read current entrypoint**

Read the existing compose-entrypoint.sh to understand what it does.

**Step 2: Remove static asset commands from entrypoint**

Replace entire file content with:

```bash
#!/usr/bin/env bash
set -eo pipefail

python manage.py migrate --noinput --skip-checks

exec "$@"
```

**Step 3: Commit entrypoint changes**

```bash
git add compose-entrypoint.sh
git commit -m "refactor(docker): simplify entrypoint to migrations only

Static asset building (tailwind, collectstatic) now happens at
image build time in the Dockerfile."
```

---

## Task 3: Build and Verify Development Image

**Files:**
- None (verification only)

**Step 1: Build the Docker image**

```bash
docker-compose build web
```

Expected: Build completes successfully with all three stages.

**Step 2: Verify container runs as non-root**

```bash
docker-compose run --rm utility whoami
```

Expected output: `app`

**Step 3: Verify user ID is 1000**

```bash
docker-compose run --rm utility id
```

Expected output: `uid=1000(app) gid=1000(app) groups=1000(app)`

**Step 4: Verify static files are present**

```bash
docker-compose run --rm utility ls -la staticfiles/ | head -10
```

Expected: Directory listing showing static files owned by app:app.

**Step 5: Verify Django can start**

```bash
docker-compose up web -d
sleep 5
curl -s http://localhost:${DJANGO_PORT:-8000}/health/ || echo "Check DJANGO_PORT in .env"
docker-compose stop web
```

Expected: Health check returns success or 200 response.

---

## Task 4: Verify Worker Container

**Files:**
- None (verification only)

**Step 1: Verify worker runs as non-root**

```bash
docker-compose run --rm worker whoami
```

Expected output: `app`

**Step 2: Verify worker can start**

```bash
docker-compose up worker -d
sleep 3
docker-compose logs worker --tail 10
docker-compose stop worker
```

Expected: Worker starts without permission errors.

---

## Task 5: Test Migration Capability

**Files:**
- None (verification only)

**Step 1: Verify migrations can run as non-root**

```bash
docker-compose run --rm utility python manage.py migrate --check
```

Expected: "No migrations to apply" or successful migration check.

**Step 2: Verify makemigrations works (development)**

```bash
docker-compose run --rm utility python manage.py makemigrations --dry-run
```

Expected: Command runs without permission errors (may show "No changes detected").

---

## Task 6: Run Full Test Suite

**Files:**
- None (verification only)

**Step 1: Run pytest to ensure nothing broke**

```bash
uv run pytest -q
```

Expected: All tests pass (417 passed as baseline).

**Step 2: Commit verification notes (optional)**

If any adjustments were needed during verification, commit them now.

---

## Task 7: Update Design Document with Implementation Notes

**Files:**
- Modify: `docs/plans/2025-12-19-non-root-docker-design.md`

**Step 1: Add implementation notes to design doc**

Append to the design document:

```markdown

## Implementation Notes

**Completed:** 2025-12-19

**Changes from design:**
- [Note any deviations from original design]

**Verification results:**
- Container runs as `app` user (UID 1000)
- Static files built at image time
- Migrations run successfully as non-root
- All 417 tests pass
```

**Step 2: Commit documentation update**

```bash
git add docs/plans/2025-12-19-non-root-docker-design.md
git commit -m "docs: add implementation notes to non-root docker design"
```

---

## Task 8: Final Cleanup and Summary

**Step 1: Review all commits**

```bash
git log --oneline main..HEAD
```

Verify commits are clean and well-described.

**Step 2: Run final verification**

```bash
docker-compose down
docker-compose up -d db redis
docker-compose build
docker-compose run --rm utility whoami
docker-compose run --rm utility python manage.py check
```

Expected: All commands succeed, whoami shows `app`.

**Step 3: Report completion**

Feature complete. Ready for PR or merge.

---

## Troubleshooting

### Permission denied errors

If you see permission errors when writing files:
1. Check that source volume mount is correct: `.:/src:cache`
2. Verify your host user UID: `id -u` (should be 1000)
3. If UID mismatch, may need to `chown -R $(id -u):$(id -g) .` on host

### Static files missing

If staticfiles directory is empty:
1. Check asset-builder stage logs during `docker-compose build`
2. Verify `STATIC_ROOT` setting in Django settings
3. Ensure `theme/static` path exists in source

### Tailwind build fails

If Tailwind build fails during image build:
1. Check Node.js installation in asset-builder stage
2. Verify `theme/` directory structure
3. Check for missing npm dependencies (may need to add npm install step)
