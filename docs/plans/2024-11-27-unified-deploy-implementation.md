# Unified Deploy Strategy Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement unified blue-green deployment infrastructure across civic-observer and corkboard with health checks, canary deployments, and shared deploy scripts.

**Architecture:** Shared deploy scripts live in `public-works` repo, synced to VPS via CI. Service repos SSH in and call scripts already on the server. Caddy health-checks both blue/green backends and auto-routes traffic. 10-minute canary period with instant rollback.

**Tech Stack:** Bash scripts, GitHub Actions, Docker Compose, Caddy reverse proxy, Slack webhooks

---

## Phase 1: Health Endpoint for Corkboard

### Task 1.1: Add health check view to corkboard

**Files:**
- Modify: `/Users/phildini/code/civicband/corkboard/config/views.py` (create if doesn't exist)
- Modify: `/Users/phildini/code/civicband/corkboard/config/urls.py`

**Step 1: Check if views.py exists and inspect urls.py**

Run:
```bash
ls -la /Users/phildini/code/civicband/corkboard/config/views.py
cat /Users/phildini/code/civicband/corkboard/config/urls.py
```

**Step 2: Create or update views.py with health check**

```python
# config/views.py
from django.http import JsonResponse


def health_check(request):
    """Health check endpoint for load balancer."""
    return JsonResponse({"status": "ok"}, status=200)
```

**Step 3: Add health endpoint to urls.py**

Add to urlpatterns:
```python
from config.views import health_check

urlpatterns = [
    # ... existing paths ...
    path("health/", health_check, name="health_check"),
]
```

**Step 4: Test locally**

Run:
```bash
cd /Users/phildini/code/civicband/corkboard
uv run python manage.py runserver 8000 &
sleep 3
curl -v http://localhost:8000/health/
# Expected: {"status": "ok"} with 200 status
kill %1
```

**Step 5: Commit**

```bash
cd /Users/phildini/code/civicband/corkboard
git add config/views.py config/urls.py
git commit -m "feat: add health check endpoint for load balancer"
```

---

## Phase 2: Create public-works Repository

### Task 2.1: Initialize public-works repo structure

**Files:**
- Create: `/Users/phildini/code/civicband/public-works/scripts/deploy.sh`
- Create: `/Users/phildini/code/civicband/public-works/scripts/lib/colors.sh`
- Create: `/Users/phildini/code/civicband/public-works/scripts/lib/health.sh`
- Create: `/Users/phildini/code/civicband/public-works/scripts/lib/notify.sh`
- Create: `/Users/phildini/code/civicband/public-works/.github/workflows/sync-to-server.yml`
- Create: `/Users/phildini/code/civicband/public-works/README.md`

**Step 1: Create directory structure**

Run:
```bash
mkdir -p /Users/phildini/code/civicband/public-works/scripts/lib
mkdir -p /Users/phildini/code/civicband/public-works/.github/workflows
cd /Users/phildini/code/civicband/public-works
git init
```

**Step 2: Create colors.sh (blue-green logic)**

```bash
#!/bin/bash
# scripts/lib/colors.sh - Blue-green deployment color management

get_current_color() {
    local state_file="$1"
    if [ -f "$state_file" ]; then
        cat "$state_file"
    else
        echo "none"
    fi
}

get_target_color() {
    local current="$1"
    if [ "$current" = "blue" ]; then
        echo "green"
    else
        echo "blue"
    fi
}

get_port() {
    local service="$1"
    local color="$2"

    case "$service" in
        civic-observer)
            [ "$color" = "blue" ] && echo "8888" || echo "8889"
            ;;
        corkboard-django)
            [ "$color" = "blue" ] && echo "8000" || echo "8001"
            ;;
        corkboard-datasette)
            [ "$color" = "blue" ] && echo "40001" || echo "40002"
            ;;
    esac
}
```

**Step 3: Create health.sh (health check functions)**

```bash
#!/bin/bash
# scripts/lib/health.sh - Health check functions

health_check() {
    local port="$1"
    local max_attempts="${2:-30}"
    local attempt=1

    log "Running health checks on port $port (max $max_attempts attempts)..."

    while [ $attempt -le $max_attempts ]; do
        if curl -f -s -o /dev/null "http://localhost:$port/health/"; then
            log_success "Health check passed (attempt $attempt/$max_attempts)"
            return 0
        fi
        log "Health check failed, retrying in 2s (attempt $attempt/$max_attempts)..."
        sleep 2
        ((attempt++))
    done

    log_error "Health checks failed after $max_attempts attempts"
    return 1
}
```

**Step 4: Create notify.sh (Slack notifications)**

```bash
#!/bin/bash
# scripts/lib/notify.sh - Slack notification functions

notify() {
    local status="$1"
    local message="$2"

    if [ -z "$SLACK_WEBHOOK_URL" ]; then
        log_warning "SLACK_WEBHOOK_URL not set, skipping notification"
        return 0
    fi

    local emoji=""
    case "$status" in
        deploying) emoji=":rocket:" ;;
        success) emoji=":white_check_mark:" ;;
        failure) emoji=":x:" ;;
        rollback) emoji=":rewind:" ;;
        canary) emoji=":bird:" ;;
    esac

    curl -s -X POST "$SLACK_WEBHOOK_URL" \
        -H 'Content-type: application/json' \
        -d "{\"text\": \"$emoji [$status] $SERVICE: $message\"}" \
        > /dev/null 2>&1 || true
}
```

**Step 5: Create main deploy.sh**

```bash
#!/bin/bash
set -euo pipefail

# Unified Blue-Green Deploy Script
# Usage: deploy.sh <service> <version>
# Services: civic-observer, corkboard-django, corkboard-datasette

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/colors.sh"
source "$SCRIPT_DIR/lib/health.sh"
source "$SCRIPT_DIR/lib/notify.sh"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log() { echo -e "${BLUE}[$(date +'%Y-%m-%d %H:%M:%S')]${NC} $*"; }
log_success() { echo -e "${GREEN}[$(date +'%Y-%m-%d %H:%M:%S')]${NC} ✓ $*"; }
log_error() { echo -e "${RED}[$(date +'%Y-%m-%d %H:%M:%S')]${NC} ✗ $*"; }
log_warning() { echo -e "${YELLOW}[$(date +'%Y-%m-%d %H:%M:%S')]${NC} ⚠ $*"; }

# Configuration
SERVICE="${1:-}"
VERSION="${2:-latest}"
CANARY_PERIOD="${CANARY_PERIOD:-600}"  # 10 minutes default

if [ -z "$SERVICE" ]; then
    log_error "Usage: deploy.sh <service> <version>"
    log_error "Services: civic-observer, corkboard-django, corkboard-datasette"
    exit 1
fi

# Service-specific configuration
case "$SERVICE" in
    civic-observer)
        DEPLOY_DIR="/home/deploy/civic-observer"
        COMPOSE_FILES="-f docker-compose.production-base.yml"
        WEB_SERVICE_PREFIX="web"
        WORKER_SERVICE_PREFIX="worker"
        HAS_WORKER=true
        ;;
    corkboard-django)
        DEPLOY_DIR="/home/deploy/corkboard"
        COMPOSE_FILES="-f docker-compose.yml"
        WEB_SERVICE_PREFIX="django"
        HAS_WORKER=false
        ;;
    corkboard-datasette)
        DEPLOY_DIR="/home/deploy/corkboard"
        COMPOSE_FILES="-f docker-compose.yml"
        WEB_SERVICE_PREFIX="sites_datasette"
        HAS_WORKER=false
        ;;
    *)
        log_error "Unknown service: $SERVICE"
        exit 1
        ;;
esac

STATE_FILE="$DEPLOY_DIR/.deployment-state-$SERVICE"
LOCKFILE="/tmp/civic-deploy.lock"

# Acquire deploy lock
exec 200>"$LOCKFILE"
if ! flock -n 200; then
    log_error "Another deploy is in progress. Exiting."
    exit 1
fi

log "========================================="
log "Unified Blue-Green Deployment"
log "Service: $SERVICE"
log "Version: $VERSION"
log "========================================="

cd "$DEPLOY_DIR"

# Determine colors
CURRENT_COLOR=$(get_current_color "$STATE_FILE")
TARGET_COLOR=$(get_target_color "$CURRENT_COLOR")
TARGET_PORT=$(get_port "$SERVICE" "$TARGET_COLOR")

log "Current active: $CURRENT_COLOR"
log "Deploying to: $TARGET_COLOR (port $TARGET_PORT)"

notify "deploying" "Starting deploy of $VERSION to $TARGET_COLOR"

# Step 1: Pull new image
log "Step 1: Pulling Docker image..."
export VERSION="$VERSION"
docker-compose $COMPOSE_FILES -f "docker-compose.$TARGET_COLOR.yml" pull || log_warning "Pull failed, using existing image"

# Step 2: Run migrations (service-specific)
if [ "$SERVICE" = "civic-observer" ]; then
    log "Step 2: Running database migrations..."
    if ! docker-compose $COMPOSE_FILES -f "docker-compose.$TARGET_COLOR.yml" \
            run --rm "$WEB_SERVICE_PREFIX-$TARGET_COLOR" python manage.py migrate --noinput; then
        log_error "Migrations failed"
        notify "failure" "Deploy failed: migrations failed"
        exit 1
    fi
elif [ "$SERVICE" = "corkboard-django" ]; then
    log "Step 2: Running database migrations..."
    if ! docker-compose $COMPOSE_FILES run --rm "${WEB_SERVICE_PREFIX}_${TARGET_COLOR}" python manage.py migrate --noinput; then
        log_error "Migrations failed"
        notify "failure" "Deploy failed: migrations failed"
        exit 1
    fi
else
    log "Step 2: Skipping migrations (not applicable for $SERVICE)"
fi

# Step 3: Start target color
log "Step 3: Starting $TARGET_COLOR stack..."
if [ "$HAS_WORKER" = true ]; then
    docker-compose $COMPOSE_FILES -f "docker-compose.$TARGET_COLOR.yml" \
        up -d "$WEB_SERVICE_PREFIX-$TARGET_COLOR" "$WORKER_SERVICE_PREFIX-$TARGET_COLOR"
else
    docker-compose $COMPOSE_FILES up -d "${WEB_SERVICE_PREFIX}_${TARGET_COLOR}"
fi

# Step 4: Wait for startup
log "Step 4: Waiting for services to start..."
sleep 10

# Step 5: Health check
log "Step 5: Running health checks..."
if ! health_check "$TARGET_PORT" 30; then
    log_error "Health checks failed"
    notify "failure" "Deploy failed: health checks failed on $TARGET_COLOR"

    # Stop the failed deployment
    if [ "$HAS_WORKER" = true ]; then
        docker-compose $COMPOSE_FILES -f "docker-compose.$TARGET_COLOR.yml" down
    else
        docker-compose $COMPOSE_FILES stop "${WEB_SERVICE_PREFIX}_${TARGET_COLOR}"
    fi
    exit 1
fi

log_success "Health checks passed!"

# Step 6: Canary period (both colors serve traffic)
if [ "$CURRENT_COLOR" != "none" ]; then
    notify "canary" "Canary started: both $CURRENT_COLOR and $TARGET_COLOR serving traffic for ${CANARY_PERIOD}s"
    log "Step 6: Canary period - both $CURRENT_COLOR and $TARGET_COLOR serving traffic..."
    log "Waiting $CANARY_PERIOD seconds ($(($CANARY_PERIOD / 60)) minutes)..."
    log "To rollback, run: touch $DEPLOY_DIR/.rollback-requested"

    sleep "$CANARY_PERIOD"

    # Check for rollback request
    if [ -f "$DEPLOY_DIR/.rollback-requested" ]; then
        log_warning "Rollback requested!"
        rm -f "$DEPLOY_DIR/.rollback-requested"

        # Stop the new deployment
        if [ "$HAS_WORKER" = true ]; then
            docker-compose $COMPOSE_FILES -f "docker-compose.$TARGET_COLOR.yml" down
        else
            docker-compose $COMPOSE_FILES stop "${WEB_SERVICE_PREFIX}_${TARGET_COLOR}"
        fi

        notify "rollback" "Rolled back: stopped $TARGET_COLOR, $CURRENT_COLOR still serving"
        log_success "Rollback complete. $CURRENT_COLOR is still serving traffic."
        exit 0
    fi

    # Canary successful - stop old color
    log "Step 7: Canary successful, stopping $CURRENT_COLOR..."
    if [ "$HAS_WORKER" = true ]; then
        docker-compose $COMPOSE_FILES -f "docker-compose.$CURRENT_COLOR.yml" down
    else
        docker-compose $COMPOSE_FILES stop "${WEB_SERVICE_PREFIX}_${CURRENT_COLOR}"
    fi
    log_success "$CURRENT_COLOR stopped"
else
    log "Step 6: No previous deployment, skipping canary period"
fi

# Step 8: Update state
echo "$TARGET_COLOR" > "$STATE_FILE"

# Step 9: Log deploy
echo "$(date -Iseconds) | $SERVICE | $VERSION | success | $TARGET_COLOR" >> /home/deploy/deploy-history.log

notify "success" "Deploy complete: $VERSION on $TARGET_COLOR"

log_success "========================================="
log_success "Deployment Complete!"
log_success "Service: $SERVICE"
log_success "Active: $TARGET_COLOR (port $TARGET_PORT)"
log_success "Version: $VERSION"
log_success "========================================="
```

**Step 6: Create GitHub Actions workflow**

Uses Tailscale SSH instead of SSH keys for better security (no long-lived keys, ACL-controlled access, audit logs).

```yaml
# .github/workflows/sync-to-server.yml
name: Sync scripts to server

on:
  push:
    branches: [main]
  workflow_dispatch:

env:
  VPS_HOSTNAME: your-vps-tailscale-hostname  # e.g., vps.tail1234.ts.net

jobs:
  sync:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Setup Tailscale
        uses: tailscale/github-action@v2
        with:
          oauth-client-id: ${{ secrets.TS_OAUTH_CLIENT_ID }}
          oauth-secret: ${{ secrets.TS_OAUTH_SECRET }}
          tags: tag:ci

      - name: Sync scripts to VPS
        run: |
          rsync -avz --delete scripts/ deploy@${{ env.VPS_HOSTNAME }}:/home/deploy/scripts/

      - name: Make scripts executable
        run: |
          ssh deploy@${{ env.VPS_HOSTNAME }} "chmod +x /home/deploy/scripts/*.sh /home/deploy/scripts/lib/*.sh"
```

**Step 7: Create README.md**

```markdown
# public-works

Shared deployment infrastructure for civic.band services.

## Overview

This repo contains deployment scripts that are synced to the VPS. Service repos (civic-observer, corkboard) SSH in and call these scripts.

## Scripts

- `scripts/deploy.sh` - Main blue-green deploy script
- `scripts/lib/colors.sh` - Blue/green color management
- `scripts/lib/health.sh` - Health check functions
- `scripts/lib/notify.sh` - Slack notifications

## Usage

```bash
# Deploy civic-observer
/home/deploy/scripts/deploy.sh civic-observer <git-sha>

# Deploy corkboard django
/home/deploy/scripts/deploy.sh corkboard-django <git-sha>

# Deploy corkboard datasette
/home/deploy/scripts/deploy.sh corkboard-datasette <git-sha>
```

## Rollback

During the 10-minute canary period, create a rollback request:

```bash
touch /home/deploy/civic-observer/.rollback-requested
# or
touch /home/deploy/corkboard/.rollback-requested
```

## Environment Variables

- `SLACK_WEBHOOK_URL` - Slack webhook for notifications
- `CANARY_PERIOD` - Canary period in seconds (default: 600)

## Required Secrets (GitHub)

- `TS_OAUTH_CLIENT_ID` - Tailscale OAuth client ID
- `TS_OAUTH_SECRET` - Tailscale OAuth client secret

## Tailscale Setup

1. Create OAuth client in Tailscale admin (Settings → OAuth clients)
2. Create ACL tag `tag:ci` with SSH access to your VPS
3. Add secrets to GitHub repo settings
```

**Step 8: Initial commit**

```bash
cd /Users/phildini/code/civicband/public-works
git add .
git commit -m "Initial commit: unified deploy scripts"
```

---

## Phase 3: Update civic-observer CI

### Task 3.1: Simplify deploy workflow to use shared scripts

**Files:**
- Modify: `/Users/phildini/code/civicband/civic-observer/.github/workflows/deploy-production.yml`

**Step 1: Read current workflow**

Run:
```bash
cat /Users/phildini/code/civicband/civic-observer/.github/workflows/deploy-production.yml
```

**Step 2: Update workflow to call shared script**

Uses Tailscale SSH for secure, keyless deployment.

```yaml
name: Deploy to Production

on:
  push:
    branches: [main]
  workflow_dispatch:

env:
  VPS_HOSTNAME: your-vps-tailscale-hostname  # e.g., vps.tail1234.ts.net

jobs:
  build:
    runs-on: ubuntu-latest
    outputs:
      version: ${{ steps.meta.outputs.version }}
    steps:
      - uses: actions/checkout@v4

      - name: Set version
        id: meta
        run: echo "version=${{ github.sha }}" >> $GITHUB_OUTPUT

      - name: Build and push Docker image
        run: |
          # Build and push to your registry
          docker build -t civic-observer:${{ steps.meta.outputs.version }} .
          # docker push ...

  deploy:
    needs: build
    runs-on: ubuntu-latest
    steps:
      - name: Setup Tailscale
        uses: tailscale/github-action@v2
        with:
          oauth-client-id: ${{ secrets.TS_OAUTH_CLIENT_ID }}
          oauth-secret: ${{ secrets.TS_OAUTH_SECRET }}
          tags: tag:ci

      - name: Deploy via SSH
        run: |
          ssh deploy@${{ env.VPS_HOSTNAME }} "/home/deploy/scripts/deploy.sh civic-observer ${{ needs.build.outputs.version }}"
```

**Step 3: Commit**

```bash
cd /Users/phildini/code/civicband/civic-observer
git add .github/workflows/deploy-production.yml
git commit -m "ci: use shared deploy script from public-works"
```

---

## Phase 4: Manual Steps (Outside This Plan)

These steps require access to systems outside these repos:

### 4.1: Update Caddyfile template in civic-band

Add to the civic-band/clerk Caddyfile template:

```caddy
(health-proxy) {
    lb_policy cookie
    lb_retries 3
    health_uri /health/
    health_interval 10s
    health_timeout 5s
}

civic.observer {
    reverse_proxy 127.0.0.1:8888 127.0.0.1:8889 {
        import health-proxy
    }
}
```

Update `(django-app)` and `civic.band` blocks similarly.

### 4.2: Create GitHub repo for public-works

1. Create new repo at github.com/[org]/public-works
2. Push local repo
3. Set up Tailscale OAuth:
   - Create OAuth client in Tailscale admin console (Settings → OAuth clients)
   - Create ACL tag `tag:ci` with SSH access to VPS
   - Add `TS_OAUTH_CLIENT_ID` and `TS_OAUTH_SECRET` to GitHub repo secrets
4. Update `VPS_HOSTNAME` in workflow file to match your Tailscale hostname

### 4.3: Set up UptimeRobot

1. Create free account at uptimerobot.com
2. Add monitors for:
   - `https://civic.observer/health/`
   - `https://civic.band/health/`
3. Configure alerts (email/SMS)

### 4.4: Create Slack webhook

1. Go to Slack App settings
2. Create incoming webhook for deploy channel
3. Add `SLACK_WEBHOOK_URL` to VPS environment

---

## Verification Checklist

After completing all phases:

- [ ] `curl https://civic.band/health/` returns `{"status": "ok"}`
- [ ] `curl https://civic.observer/health/` returns `{"status": "ok"}`
- [ ] `/home/deploy/scripts/deploy.sh` exists on VPS
- [ ] Test deploy: `deploy.sh civic-observer test-version`
- [ ] Slack notification received
- [ ] UptimeRobot monitors showing UP
- [ ] Rollback test: `touch /home/deploy/civic-observer/.rollback-requested` during canary
