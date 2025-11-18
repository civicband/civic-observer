# Claude Code Project Configuration

## Project Commands

**IMPORTANT**: This project uses Docker for running the application and `uv` for local development tools.

### Main Development Commands (via justfile):
- `just dev` - Start all services (web, worker, db, redis)
- `just migrate` - Run database migrations
- `just manage <command>` - Run any Django management command
- `just test` - Run test suite
- `just dbshell` - Access PostgreSQL shell
- `just logs` - View logs for all services
- `just stop` - Stop all services
- `just down` - Stop and remove all containers

### Direct Docker Commands:
- `docker-compose up` - Start all services
- `docker-compose run --rm utility python manage.py <command>` - Run Django commands
- `docker-compose exec web <command>` - Run command in running web container
- `docker-compose exec db psql -U postgres` - Access PostgreSQL

### Local Development Tools (via uv):
- `uv run pytest` - Run tests (connects to Docker PostgreSQL)
- `uv run --group dev ruff check .` - Run linting
- `uv run --group dev mypy .` - Run type checking
- `uv sync` - Install/sync dependencies
- `uv add <package>` - Add new dependencies

## Project Structure

This is a Django project for civic monitoring with the following key apps:
- `municipalities` - Municipality data and webhook API
- `searches` - Search functionality and saved searches
- `meetings` - Meeting documents (agendas and minutes) from civic.band
- `users` - User management

## Database Configuration

This project uses **PostgreSQL** for all environments (development, production, and testing).

### Database Setup:
- Database runs in Docker via `docker-compose` (service name: `db`)
- PostgreSQL 17 (Alpine) - latest stable version
- Connection configured via `DATABASE_URL` environment variable
- **Port 5433** on localhost (mapped to avoid conflict with local PostgreSQL on 5432)
- Default URLs:
  - Development/Production (inside Docker): `postgres://postgres@db/postgres`
  - Testing (outside Docker): `postgres://postgres@localhost:5433/postgres`

### Key Commands:
- `docker-compose up db` - Start PostgreSQL service
- `just dbshell` - Access PostgreSQL shell (shortcut)
- `just migrate` - Run database migrations
- `just makemigrations` - Create new migrations
- `docker-compose exec db psql -U postgres` - Direct PostgreSQL access
- `psql -h localhost -p 5433 -U postgres` - Access database from host machine

### Important Notes:
- **All environments use PostgreSQL** (no SQLite)
- Database must be running via Docker for development and testing
- Port 5433 is used to avoid conflicts with any local PostgreSQL on port 5432
- Migrations are shared across all environments
- Use `.env` file to override `DATABASE_URL` if needed

## Email Configuration

The project uses django-anymail with Postmark for email delivery. Email templates are in `templates/email/`.

## Background Tasks (django-rq)

This project uses django-rq with Redis for background task processing. Tasks include:
- Meeting data backfill from civic.band API (triggered by webhooks or admin actions)

### Key Commands:
- `uv run python manage.py rqworker default` - Start an RQ worker (already configured in docker-compose)
- Visit `/django-rq/` when logged in as admin to see the RQ dashboard

### Configuration:
- Redis runs on port 6379 in the `redis` service (docker-compose)
- Queue configuration is in `config/settings/base.py`
- Tasks are defined in `meetings/tasks.py`
- Tests run tasks synchronously (configured in `tests/conftest.py`)

## Testing

All tests should be run with `uv run pytest`. The project has high test coverage requirements.

### Running Tests:
- **Prerequisites**: Docker services must be running (`docker-compose up db redis`)
- `uv run pytest` - Run all tests
- `uv run pytest --cov` - Run with coverage report
- `just test` - Run tests via justfile command
- Tests automatically configure RQ to run tasks synchronously
- Tests use PostgreSQL database (same as development/production)
