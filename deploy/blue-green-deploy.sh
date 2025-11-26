#!/bin/bash
set -euo pipefail

# Blue-Green Deployment Script for Civic Observer
# Usage: ./blue-green-deploy.sh <version>
# Example: ./blue-green-deploy.sh main-abc123

VERSION="${1:-latest}"
DEPLOY_DIR="/home/deploy/civic-observer"
STATE_FILE="$DEPLOY_DIR/.deployment-state"
CADDY_CONFIG="$DEPLOY_DIR/deploy/Caddyfile"
CADDY_RELOAD_CMD="sudo systemctl reload caddy"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log() { echo -e "${BLUE}[$(date +'%Y-%m-%d %H:%M:%S')]${NC} $*"; }
log_success() { echo -e "${GREEN}[$(date +'%Y-%m-%d %H:%M:%S')]${NC} ✓ $*"; }
log_error() { echo -e "${RED}[$(date +'%Y-%m-%d %H:%M:%S')]${NC} ✗ $*"; }
log_warning() { echo -e "${YELLOW}[$(date +'%Y-%m-%d %H:%M:%S')]${NC} ⚠ $*"; }

# Determine current and target colors
get_current_color() {
    if [ -f "$STATE_FILE" ]; then
        cat "$STATE_FILE"
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
    local color="$1"
    if [ "$color" = "blue" ]; then
        echo "8888"
    else
        echo "8889"
    fi
}

# Health check function
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

# Database migration function
run_migrations() {
    local color="$1"
    log "Running database migrations..."

    if docker-compose -f docker-compose.production-base.yml \
                      -f "docker-compose.$color.yml" \
                      run --rm "web-$color" python manage.py migrate --noinput; then
        log_success "Migrations completed successfully"
        return 0
    else
        log_error "Migrations failed"
        return 1
    fi
}

# Update Caddyfile to point to new backend
update_caddy() {
    local port="$1"
    log "Updating Caddy configuration to point to port $port..."

    # Backup current config
    cp "$CADDY_CONFIG" "$CADDY_CONFIG.bak"

    # Update the reverse_proxy line
    sed -i "s/reverse_proxy localhost:[0-9]\+/reverse_proxy localhost:$port/" "$CADDY_CONFIG"

    # Reload Caddy
    if $CADDY_RELOAD_CMD; then
        log_success "Caddy configuration updated and reloaded"
        return 0
    else
        log_error "Failed to reload Caddy"
        # Restore backup
        mv "$CADDY_CONFIG.bak" "$CADDY_CONFIG"
        return 1
    fi
}

# Main deployment logic
main() {
    log "========================================="
    log "Civic Observer Blue-Green Deployment"
    log "Version: $VERSION"
    log "========================================="

    cd "$DEPLOY_DIR"

    # Determine colors
    CURRENT_COLOR=$(get_current_color)
    TARGET_COLOR=$(get_target_color "$CURRENT_COLOR")
    TARGET_PORT=$(get_port "$TARGET_COLOR")

    log "Current active: $CURRENT_COLOR"
    log "Deploying to: $TARGET_COLOR (port $TARGET_PORT)"

    # Step 1: Ensure shared services are running
    log "Step 1: Ensuring shared services (DB, Redis, PgBouncer) are running..."
    docker-compose -f docker-compose.production-base.yml up -d
    sleep 5
    log_success "Shared services ready"

    # Step 2: Pull/build new image
    log "Step 2: Pulling Docker image version $VERSION..."
    export VERSION="$VERSION"
    if docker-compose -f "docker-compose.$TARGET_COLOR.yml" pull; then
        log_success "Image pulled successfully"
    else
        log_warning "Pull failed, will try to use existing image"
    fi

    # Step 3: Run migrations (before starting new services)
    log "Step 3: Running database migrations..."
    if ! run_migrations "$TARGET_COLOR"; then
        log_error "Deployment failed: Migrations failed"
        exit 1
    fi

    # Step 4: Start target color stack
    log "Step 4: Starting $TARGET_COLOR stack..."
    if docker-compose -f docker-compose.production-base.yml \
                      -f "docker-compose.$TARGET_COLOR.yml" \
                      up -d "web-$TARGET_COLOR" "worker-$TARGET_COLOR"; then
        log_success "$TARGET_COLOR stack started"
    else
        log_error "Failed to start $TARGET_COLOR stack"
        exit 1
    fi

    # Step 5: Wait for services to be healthy
    log "Step 5: Waiting for $TARGET_COLOR services to become healthy..."
    sleep 10

    # Step 6: Run health checks
    log "Step 6: Running health checks..."
    if ! health_check "$TARGET_PORT" 30; then
        log_error "Deployment failed: Health checks failed"
        log "Rolling back by stopping $TARGET_COLOR stack..."
        docker-compose -f "docker-compose.$TARGET_COLOR.yml" down
        exit 1
    fi

    # Step 7: Switch traffic to new color
    log "Step 7: Switching traffic to $TARGET_COLOR..."
    if ! update_caddy "$TARGET_PORT"; then
        log_error "Deployment failed: Caddy update failed"
        log "Rolling back by stopping $TARGET_COLOR stack..."
        docker-compose -f "docker-compose.$TARGET_COLOR.yml" down
        exit 1
    fi

    # Step 8: Grace period before stopping old color
    if [ "$CURRENT_COLOR" != "none" ]; then
        log "Step 8: Grace period (60s) before stopping $CURRENT_COLOR stack..."
        log "This allows in-flight requests to complete..."
        sleep 60

        # Step 9: Stop old color
        log "Step 9: Stopping $CURRENT_COLOR stack..."
        docker-compose -f "docker-compose.$CURRENT_COLOR.yml" down
        log_success "$CURRENT_COLOR stack stopped"
    else
        log "Step 8: No previous deployment to clean up"
    fi

    # Step 10: Update state file
    echo "$TARGET_COLOR" > "$STATE_FILE"
    log_success "Deployment state updated"

    # Step 11: Cleanup old images
    log "Step 11: Cleaning up old Docker images..."
    docker image prune -f --filter "until=24h" || true

    log_success "========================================="
    log_success "Deployment Complete!"
    log_success "Active deployment: $TARGET_COLOR (port $TARGET_PORT)"
    log_success "Version: $VERSION"
    log_success "========================================="

    # Show running containers
    log "Active containers:"
    docker ps --filter "label=civic.deployment.color=$TARGET_COLOR" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
}

# Rollback function (for manual use)
rollback() {
    log "========================================="
    log "Rolling back to previous deployment"
    log "========================================="

    CURRENT_COLOR=$(get_current_color)
    if [ "$CURRENT_COLOR" = "none" ]; then
        log_error "No active deployment to rollback from"
        exit 1
    fi

    PREVIOUS_COLOR=$(get_target_color "$CURRENT_COLOR")

    log "Current: $CURRENT_COLOR"
    log "Rolling back to: $PREVIOUS_COLOR"

    # Check if previous stack is still running
    if docker ps --filter "name=web-$PREVIOUS_COLOR" --format "{{.Names}}" | grep -q "web-$PREVIOUS_COLOR"; then
        log_success "Previous $PREVIOUS_COLOR stack is still running"

        # Just switch Caddy back
        PREVIOUS_PORT=$(get_port "$PREVIOUS_COLOR")
        update_caddy "$PREVIOUS_PORT"
        echo "$PREVIOUS_COLOR" > "$STATE_FILE"

        log_success "Rollback complete"
    else
        log_error "Previous $PREVIOUS_COLOR stack is not running, cannot rollback"
        log "You may need to run a forward deployment instead"
        exit 1
    fi
}

# Handle script arguments
case "${1:-deploy}" in
    deploy)
        shift
        main "$@"
        ;;
    rollback)
        rollback
        ;;
    *)
        log_error "Unknown command: $1"
        echo "Usage: $0 {deploy <version>|rollback}"
        exit 1
        ;;
esac
