# Civic Observer Blue-Green Deployment

This directory contains the configuration and scripts for blue-green deployments to production.

## Overview

The deployment system uses:
- **Docker Compose** for container orchestration
- **Caddy** for reverse proxy and automatic HTTPS
- **Tailscale SSH** for secure GitHub Actions → VPS connectivity (no SSH keys needed!)
- **Blue-Green Strategy** for zero-downtime deployments

## Architecture

### Deployment Colors

- **Blue Stack**: Runs on port 8888
- **Green Stack**: Runs on port 8889
- **Shared Services**: PostgreSQL, Redis, PgBouncer (single instances)

At any time, one color is "active" (serving traffic via Caddy), while the other is either:
- Stopped (during normal operation)
- Starting up (during deployment)
- Draining connections (after traffic switch)

### Traffic Flow

```
Internet → Caddy (:80, :443) → Active Color (:8888 or :8889) → Django/Uvicorn
                                     ↓
                          Shared DB + Redis + PgBouncer
```

## Files

### Docker Compose Files

- **`docker-compose.production-base.yml`** - Shared services (DB, Redis, PgBouncer)
- **`docker-compose.blue.yml`** - Blue stack (web-blue, worker-blue on port 8888)
- **`docker-compose.green.yml`** - Green stack (web-green, worker-green on port 8889)

### Deployment Scripts

- **`blue-green-deploy.sh`** - Main deployment orchestration script
- **`status.sh`** - Deployment status checker
- **`Caddyfile`** - Caddy reverse proxy configuration

### GitHub Actions

- **`.github/workflows/deploy-production.yml`** - CI/CD workflow for automatic deployments

## Deployment Flow

1. **Trigger**: CI passes on `main` branch
2. **Build**: Docker image is built and tagged with `main-<sha>`
3. **Transfer**: Image is transferred to VPS via Tailscale SSH
4. **Deploy**: `blue-green-deploy.sh` orchestrates the deployment:
   - Determines current active color (blue or green)
   - Deploys to inactive color
   - Runs database migrations
   - Starts new containers
   - Runs health checks (30 attempts, 2s intervals)
   - Updates Caddy configuration to point to new color
   - Reloads Caddy (traffic switches to new deployment)
   - Grace period (60s for connection draining)
   - Stops old color containers
   - Updates deployment state

## Initial Setup

### 1. VPS Prerequisites

```bash
# Install Docker and Docker Compose
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker deploy

# Install Caddy
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update
sudo apt install caddy

# Install Tailscale
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up

# Enable Tailscale SSH (no SSH keys needed!)
sudo tailscale set --ssh

# Create deployment directory
cd /home/deploy
git clone https://github.com/civicband/civic-observer.git
cd civic-observer

# Create .env.production file
cp .env.example .env.production
# Edit .env.production with production values

# Setup Caddy
sudo mkdir -p /etc/caddy
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile
sudo systemctl enable caddy
sudo systemctl start caddy

# Create Docker network
docker network create civic-network

# Start shared services
docker-compose -f docker-compose.production-base.yml up -d

# Initial deployment (to blue)
./deploy/blue-green-deploy.sh main-initial
```

### 2. Tailscale Setup

**On your VPS:**
```bash
# Install and enable Tailscale
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up

# Enable Tailscale SSH (replaces traditional SSH)
sudo tailscale set --ssh
```

**In Tailscale Admin Console:**
1. Create OAuth credentials for CI/CD
2. Set appropriate tags (e.g., `tag:ci`) for GitHub Actions
3. Configure ACLs to allow GitHub Actions to SSH to your VPS

**Tailscale ACL example:**
```json
{
  "tagOwners": {
    "tag:ci": ["autogroup:admin"]
  },
  "acls": [
    {
      "action": "accept",
      "src": ["tag:ci"],
      "dst": ["tag:server:22"],
      "users": ["deploy"]
    }
  ],
  "ssh": [
    {
      "action": "accept",
      "src": ["tag:ci"],
      "dst": ["tag:server"],
      "users": ["deploy"]
    }
  ]
}
```

### 3. GitHub Secrets Setup

Add these secrets to your GitHub repository (Settings → Secrets and variables → Actions):

- **`TS_OAUTH_CLIENT_ID`** - Tailscale OAuth client ID
- **`TS_OAUTH_SECRET`** - Tailscale OAuth secret
- **`PRODUCTION_USER`** - SSH username (e.g., `deploy`)
- **`PRODUCTION_HOST`** - VPS Tailscale hostname (e.g., `civic-vps.your-tailnet.ts.net`)

**No SSH key needed!** Tailscale SSH handles authentication automatically.

### 4. VPS User Setup

```bash
# Give deploy user Caddy reload permission without password
echo "deploy ALL=(ALL) NOPASSWD: /bin/systemctl reload caddy" | sudo tee /etc/sudoers.d/deploy-caddy
sudo chmod 0440 /etc/sudoers.d/deploy-caddy
```

## Manual Deployment

To manually deploy from the VPS:

```bash
cd /home/deploy/civic-observer

# Pull latest code
git pull origin main

# Build image locally
docker build -t civic-observer:manual-$(date +%Y%m%d-%H%M%S) .

# Deploy
./deploy/blue-green-deploy.sh manual-$(date +%Y%m%d-%H%M%S)
```

## Manual Rollback

If the previous deployment is still running (within 60s grace period), you can rollback:

```bash
cd /home/deploy/civic-observer
./deploy/blue-green-deploy.sh rollback
```

⚠️ **Note**: Rollback only works if the previous color is still running. After the grace period, the old containers are stopped and you must do a forward deployment instead.

## Monitoring

### Check Deployment Status

```bash
# Quick status check
cd /home/deploy/civic-observer
./deploy/status.sh
```

This shows:
- Active deployment color
- Running containers
- Health check status for both colors
- Caddy status and backend
- Docker disk usage

### Check Active Deployment

```bash
# View current active color
cat /home/deploy/civic-observer/.deployment-state

# View running containers
docker ps --filter "label=civic.deployment.color"

# View logs
docker logs -f civic-web-blue    # or civic-web-green
docker logs -f civic-worker-blue # or civic-worker-green
```

### Check Caddy Status

```bash
# View Caddy logs
sudo journalctl -u caddy -f

# Check Caddy config
sudo caddy fmt --config /etc/caddy/Caddyfile

# Reload Caddy manually
sudo systemctl reload caddy
```

### Health Checks

```bash
# Check application health
curl http://localhost:8888/health/  # Blue
curl http://localhost:8889/health/  # Green
curl https://civic.observer/health/  # Active via Caddy

# Check Caddy health
curl http://localhost:8080/health
```

## Troubleshooting

### Deployment Fails at Health Check

- Check container logs: `docker logs civic-web-{blue|green}`
- Verify database connectivity: `docker-compose -f docker-compose.production-base.yml ps`
- Check migrations: `docker-compose -f docker-compose.{blue|green}.yml run --rm web-{blue|green} python manage.py showmigrations`

### Traffic Not Switching

- Verify Caddyfile was updated: `sudo cat /etc/caddy/Caddyfile`
- Check Caddy logs: `sudo journalctl -u caddy -n 50`
- Manually reload Caddy: `sudo systemctl reload caddy`

### Database Migration Issues

Migrations run before the new stack starts, on the shared database. If migrations fail:

1. Check migration logs in deployment output
2. Manually run migrations: `docker-compose -f docker-compose.{color}.yml run --rm web-{color} python manage.py migrate`
3. If migration is broken, fix and re-deploy

### Port Already in Use

If deployment fails because port is in use:

```bash
# Check what's using the port
sudo lsof -i :8888  # or :8889

# If it's the wrong color, stop it
docker-compose -f docker-compose.{color}.yml down
```

### Tailscale SSH Issues

```bash
# Verify Tailscale SSH is enabled
tailscale status --json | jq .Self.Capabilities

# Test SSH from another machine
ssh deploy@civic-vps.your-tailnet.ts.net

# Check Tailscale logs
sudo journalctl -u tailscaled -f
```

## Security Considerations

1. **No SSH Keys**: Tailscale SSH uses WireGuard and centralized auth - no keys to manage or rotate
2. **Tailscale ACLs**: Control exactly which devices can SSH to your VPS
3. **Secrets**: Never commit `.env.production` - use GitHub Secrets
4. **Caddy**: Automatically handles HTTPS with Let's Encrypt
5. **Docker**: Runs as non-root user inside containers
6. **Least Privilege**: Deploy user only has permission to reload Caddy (via sudoers)

## Advantages of Tailscale SSH

✅ **No SSH key management** - No keys to generate, distribute, or rotate
✅ **Centralized auth** - Use Tailscale identity provider (Google, GitHub, etc.)
✅ **Automatic encryption** - WireGuard by default
✅ **Audit logs** - See who accessed what in Tailscale admin
✅ **Easy revocation** - Remove device from tailnet to revoke access
✅ **No exposed ports** - SSH only accessible via Tailscale network

## Future Improvements

- [ ] Add Slack/email notifications on deployment success/failure
- [ ] Implement canary deployments (gradual traffic shift)
- [ ] Add automated smoke tests after traffic switch
- [ ] Integrate with monitoring (Sentry, New Relic, etc.)
- [ ] Add database backup before migrations
- [ ] Implement deployment locks for maintenance windows
- [ ] Add prometheus metrics export from containers
