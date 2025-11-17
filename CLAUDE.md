# Claude Code Project Configuration

## Project Commands

**IMPORTANT**: This project uses `uv` for dependency management and command execution.

### Always use `uv run` for Python commands:
- `uv run python manage.py <command>` - Django management commands
- `uv run pytest` - Run tests
- `uv run python <script.py>` - Run Python scripts

### Other important commands:
- `uv sync` - Install/sync dependencies
- `uv add <package>` - Add new dependencies

## Project Structure

This is a Django project for civic monitoring with the following key apps:
- `municipalities` - Municipality data and webhook API
- `searches` - Search functionality and saved searches
- `meetings` - Meeting documents (agendas and minutes) from civic.band
- `users` - User management

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
- `uv run pytest` - Run all tests
- `uv run pytest --cov` - Run with coverage report
- Tests automatically configure RQ to run tasks synchronously
