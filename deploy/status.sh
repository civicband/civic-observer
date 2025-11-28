#!/bin/bash
# Deployment Status Checker for Civic Observer

set -euo pipefail

DEPLOY_DIR="/home/deploy/civic-observer"
STATE_FILE="$DEPLOY_DIR/.deployment-state-civic-observer"
DEPLOY_HISTORY="/home/deploy/deploy-history.log"

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}Civic Observer Deployment Status${NC}"
echo -e "${BLUE}========================================${NC}"
echo

# Current active color
if [ -f "$STATE_FILE" ]; then
    ACTIVE_COLOR=$(cat "$STATE_FILE")
    echo -e "${GREEN}Active Deployment:${NC} $ACTIVE_COLOR"
else
    echo -e "${YELLOW}Active Deployment:${NC} none (first deployment pending)"
fi
echo

# Container status
echo -e "${BLUE}Running Containers:${NC}"
docker ps --filter "label=civic.deployment.color" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" || echo "None"
echo

# Shared services status
echo -e "${BLUE}Shared Services:${NC}"
docker ps --filter "name=civic-observer-db" --filter "name=redis" --filter "name=pgbouncer" --format "table {{.Names}}\t{{.Status}}" || echo "Not running"
echo

# Health checks
echo -e "${BLUE}Health Checks:${NC}"

# Blue
if docker ps --filter "name=web-blue" --format "{{.Names}}" | grep -q "web-blue"; then
    if curl -f -s -o /dev/null "http://localhost:8888/health/"; then
        echo -e "  Blue (8888):  ${GREEN}✓ Healthy${NC}"
    else
        echo -e "  Blue (8888):  ${RED}✗ Unhealthy${NC}"
    fi
else
    echo -e "  Blue (8888):  ${YELLOW}- Not running${NC}"
fi

# Green
if docker ps --filter "name=web-green" --format "{{.Names}}" | grep -q "web-green"; then
    if curl -f -s -o /dev/null "http://localhost:8889/health/"; then
        echo -e "  Green (8889): ${GREEN}✓ Healthy${NC}"
    else
        echo -e "  Green (8889): ${RED}✗ Unhealthy${NC}"
    fi
else
    echo -e "  Green (8889): ${YELLOW}- Not running${NC}"
fi

# Production (via Caddy)
if curl -f -s -o /dev/null "https://civic.observer/health/"; then
    echo -e "  Production:   ${GREEN}✓ Healthy${NC}"
else
    echo -e "  Production:   ${RED}✗ Unhealthy${NC}"
fi
echo

# Caddy status
echo -e "${BLUE}Caddy Status:${NC}"
if systemctl is-active --quiet caddy; then
    echo -e "  ${GREEN}✓ Running${NC}"
    echo -e "  Backends: 8888 (blue), 8889 (green) - Caddy routes to healthy"
else
    echo -e "  ${RED}✗ Not running${NC}"
fi
echo

# Recent deployments (if log exists)
if [ -f "$DEPLOY_HISTORY" ]; then
    echo -e "${BLUE}Recent Deployments:${NC}"
    grep "civic-observer" "$DEPLOY_HISTORY" | tail -n 5 || echo "  No recent deployments"
    echo
fi

# Disk usage
echo -e "${BLUE}Docker Disk Usage:${NC}"
docker system df
echo

echo -e "${BLUE}========================================${NC}"
