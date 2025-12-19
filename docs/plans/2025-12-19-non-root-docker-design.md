# Non-Root Docker Container Design

## Overview

Configure Docker containers to run as a non-root user (`app` with UID 1000) for improved security. This applies to both development and production environments.

## Goals

- Run Django application containers as non-root user
- Build static assets at image build time (not runtime)
- Maintain seamless development experience with volume mounts
- Keep production images self-contained and immutable

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Environments | Both dev and prod | Consistency prevents deployment surprises |
| Static assets | Build at image time | Faster startup, immutable images |
| User UID | 1000 | Matches default Linux user, Docker Desktop handles mapping |
| User name | `app` | Simple, generic, framework-agnostic |
| Image strategy | Single image with static files | Self-contained, reproducible deployments |

## Dockerfile Changes

Restructure into three stages:

### Stage 1: Builder (unchanged concept)
- Install `uv`, compile and install Python dependencies
- Runs as root (fine for build-time)

### Stage 2: Asset Builder (new)
- Copy source code
- Run `tailwind build` and `collectstatic`
- Output compiled static files to known location

### Stage 3: Release
- Create `app` user with UID/GID 1000
- Copy Python packages from builder stage
- Copy pre-built static files from asset-builder stage
- Set ownership of `/src` to `app:app`
- Set `USER app` before `CMD`

```dockerfile
# Example structure (not complete):

FROM python:3.13-slim-bookworm AS builder
# ... install dependencies ...

FROM builder AS asset-builder
COPY . /src/
WORKDIR /src/
RUN python manage.py tailwind build
RUN python manage.py collectstatic --noinput

FROM python:3.13-slim-bookworm AS release
# Create non-root user
RUN groupadd --gid 1000 app && \
    useradd --uid 1000 --gid 1000 --create-home app

# Copy dependencies from builder
COPY --from=builder /usr/local/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages

# Copy application code and static files
COPY --chown=app:app . /src/
COPY --from=asset-builder --chown=app:app /src/staticfiles /src/staticfiles

WORKDIR /src/
USER app

CMD ["uvicorn", "config.asgi:application", "--host", "0.0.0.0"]
```

## Entrypoint Script Changes

Current entrypoint does three things:
1. `tailwind build` — **moves to Dockerfile**
2. `collectstatic` — **moves to Dockerfile**
3. `migrate` — **stays in entrypoint**

New `compose-entrypoint.sh`:

```bash
#!/usr/bin/env bash
set -eo pipefail

python manage.py migrate --noinput --skip-checks

exec "$@"
```

Migrations can run as `app` user — they only need database access, not filesystem write access.

## Docker Compose Changes

### Development (`docker-compose.yml`)

Minimal changes required:

- Volume mount `.:/src:cache` stays the same (UID 1000 matches typical host users)
- No `user:` directive needed (Dockerfile handles it)
- Third-party images unchanged (postgres, redis, pgbouncer manage their own users)

### Production (`docker-compose.blue.yml`, `docker-compose.green.yml`)

No changes needed:

- Image runs as non-root via Dockerfile
- Entrypoint still runs migrations before starting services
- Static files baked into image, served by whitenoise/reverse proxy

## Third-Party Images

These already handle non-root appropriately:

| Image | User | Notes |
|-------|------|-------|
| `postgres:17-alpine` | `postgres` | Dev only, runs as postgres user |
| `redis:7-alpine` | `redis` | Runs as redis user |
| `edoburu/pgbouncer` | configured | Manages its own user |

## Testing & Verification

After implementing, verify with:

```bash
# Check container runs as non-root
docker-compose run --rm utility whoami
# Expected: app

docker-compose run --rm utility id
# Expected: uid=1000(app) gid=1000(app) groups=1000(app)

# Verify static files are present
docker-compose run --rm utility ls -la staticfiles/

# Verify migrations can run
docker-compose run --rm utility python manage.py migrate --check
```

## Potential Issues

1. **Host UID mismatch**: If your host user isn't UID 1000, new files created by the container may have different ownership. Rare on standard setups.

2. **Unexpected write locations**: Code attempting to write outside `/src` will fail with permission errors. This is a feature — it surfaces hidden assumptions.

3. **Third-party packages**: Packages expecting system path write access will fail. Rare, but watch for it.

## Security Benefits

- Container compromise gives attacker limited privileges
- No root access to escape to host or modify system files
- Follows principle of least privilege
- Aligns with container security best practices (CIS Docker Benchmark)

## Files to Modify

1. `Dockerfile` — Restructure stages, add user creation
2. `compose-entrypoint.sh` — Remove tailwind/collectstatic commands
3. No changes needed to docker-compose files

## Out of Scope

- Modifying third-party images (postgres, redis, pgbouncer)
- Rootless Docker daemon configuration
- Kubernetes-specific security contexts

## Implementation Notes

**Completed:** 2025-12-19

**Changes from design:**
- Static file path is `/src/static` (not `/src/staticfiles`) to match `STATIC_ROOT` in Django settings
- Tailwind CSS outputs to `/src/frontend/css/tailwind.css`, copied separately from static files
- No `theme/static` directory exists in this project; removed from Dockerfile

**Verification results:**
- Container runs as `app` user (UID 1000, GID 1000)
- Static files built at image time and present in container
- Tailwind CSS (92KB) built and available at `/src/frontend/css/tailwind.css`
- Django check passes
- Migrations run successfully as non-root
- All 417 tests pass (83.9% coverage)
