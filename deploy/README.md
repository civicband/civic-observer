# Civic Observer Deployment

This directory contains Docker Compose files for blue-green deployments.

## Overview

Deployment is handled by shared scripts in the [public-works](https://github.com/civicband/public-works) repository. This repo only contains the Docker Compose configuration.

**Key points:**
- Deploy scripts live in `public-works`, synced to VPS at `/home/deploy/scripts/`
- Caddy configuration lives in `civic-band` (managed by clerk)
- CI workflow triggers deploy via Tailscale SSH

## Architecture

```
Internet → Caddy → Blue (8888) or Green (8889) → Django/Uvicorn
                          ↓
               Shared DB + Redis + PgBouncer
```

### Deployment Colors
- **Blue**: Port 8888
- **Green**: Port 8889
- **Shared Services**: PostgreSQL, Redis, PgBouncer

### Deployment Flow
1. CI passes on `main` branch
2. GitHub Actions connects to VPS via Tailscale SSH
3. Git pull + Docker build on VPS
4. `deploy.sh` orchestrates blue-green switch with 10-minute canary period

## Files

### Docker Compose (in repo root)
- `docker-compose.production.yml` - CivicObserver stack: shared Redis plus the blue (8888) and green (8889) web/worker pairs
- PostgreSQL and PgBouncer are external to this repo; the app connects via `DATABASE_URL`

### Scripts (in this directory)
- `status.sh` - Check deployment status on VPS

### External Dependencies
- **Deploy scripts**: [public-works](https://github.com/civicband/public-works) repo
- **Caddy config**: civic-band repo (managed by clerk)

## Manual Operations

### Check Status
```bash
ssh deploy@<vps-tailscale-hostname>
/home/deploy/scripts/status.sh
# or
/home/deploy/civic-observer/deploy/status.sh
```

### Manual Deploy
```bash
ssh deploy@<vps-tailscale-hostname>
cd /home/deploy/civic-observer
git pull origin main
docker build -t civic-observer:manual-$(date +%Y%m%d) .
/home/deploy/scripts/deploy.sh civic-observer manual-$(date +%Y%m%d)
```

### Rollback During Canary
During the 10-minute canary period, both colors serve traffic. To rollback:
```bash
ssh deploy@<vps-tailscale-hostname>
touch /home/deploy/civic-observer/.rollback-requested
```
The deploy script will stop the new color and keep the old one running.

### Health Checks
```bash
# Direct to colors
curl http://localhost:8888/health/  # Blue
curl http://localhost:8889/health/  # Green

# Via Caddy
curl https://civic.observer/health/
```

## Initial VPS Setup

### Prerequisites
```bash
# Install Docker
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker deploy

# Install Tailscale and enable SSH
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
sudo tailscale set --ssh

# Clone repo
cd /home/deploy
git clone https://github.com/civicband/civic-observer.git

# Create production env
cp civic-observer/.env.example civic-observer/.env.production
# Edit with production values. Required:
#   DJANGO_SETTINGS_MODULE=config.settings.production
#   SECRET_KEY=<unique random value> (production refuses to start without one)
#   DATABASE_URL, ALLOWED_HOSTS, REDIS_URL, POSTMARK_SERVER_TOKEN, SENTRY_DSN, ...
# Never commit this file.

# Create Docker network
docker network create civic-network

# Start the stack (shared Redis plus blue/green web+worker pairs).
# PostgreSQL/PgBouncer are external and reached via DATABASE_URL.
cd civic-observer
docker-compose -f docker-compose.production.yml up -d
```

### GitHub Secrets Required
- `TS_OAUTH_CLIENT_ID` - Tailscale OAuth client ID
- `TS_OAUTH_SECRET` - Tailscale OAuth secret
- `VPS_HOSTNAME` - VPS Tailscale hostname
- `VPS_KNOWN_HOSTS` - Pinned SSH host key(s) for the VPS (from a trusted
  network: `ssh-keyscan -t ed25519 <hostname>`). Required so deploys verify
  the host instead of disabling host-key checking.

## Troubleshooting

### Deployment Fails
```bash
# Check container logs
docker logs civic-web-blue   # or civic-web-green
docker logs civic-worker-blue

# Check shared services
docker-compose -f docker-compose.production.yml ps
```

### Health Check Fails
```bash
# Check if container is running
docker ps --filter "label=civic.deployment.color"

# Check application logs
docker logs civic-web-blue --tail 100
```

### Database Issues
PostgreSQL and PgBouncer run outside this compose stack; check them at their
host (see `DATABASE_URL`). For app-side connectivity:

```bash
# Run migrations manually against the configured DATABASE_URL
docker-compose -f docker-compose.production.yml \
  run --rm web-blue python manage.py migrate
```
