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
- `users` - User management

## Email Configuration

The project uses django-anymail with Postmark for email delivery. Email templates are in `templates/email/`.

## Testing

All tests should be run with `uv run pytest`. The project has high test coverage requirements.
