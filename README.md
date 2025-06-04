# CivicObserver

A Django application for civic monitoring.

## Developer Setup

### Prerequisites

- Python 3.13.3 or higher
- [uv](https://docs.astral.sh/uv/) for dependency management

### Getting Started

1. **Clone the repository**
   ```bash
   git clone <repository-url>
   cd civic-observer
   ```

2. **Install dependencies**
   ```bash
   uv sync --group dev --group test
   ```

3. **Run database migrations**
   ```bash
   uv run python manage.py migrate
   ```

4. **Create a superuser (optional)**
   ```bash
   uv run python manage.py createsuperuser
   ```

5. **Start the development server**
   ```bash
   uv run python manage.py runserver
   ```

The application will be available at `http://localhost:8000`.

## Development Workflow

### Running Tests

Run the full test suite with coverage:
```bash
uv run pytest
```

Run tests with coverage report:
```bash
uv run pytest --cov
```

Run tests in fast mode (stop on first failure):
```bash
uv run pytest -x --ff
```

### Code Quality

This project uses several tools to maintain code quality:

**Linting and formatting:**
```bash
uv run ruff check .
uv run ruff check --fix .  # Auto-fix issues
```

**Type checking:**
```bash
uv run mypy .
```

**Run all checks:**
```bash
uv run pytest && uv run ruff check . && uv run mypy .
```

### Static Files

Static files are served using WhiteNoise. To collect static files:
```bash
uv run python manage.py collectstatic
```

## Configuration

The project uses environment-based settings:
- `config.settings.development` - Development (default)
- `config.settings.production` - Production

Set the `DJANGO_SETTINGS_MODULE` environment variable to switch between configurations.
