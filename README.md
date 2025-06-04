# CivicObserver

A Django application for civic monitoring.

## Developer Setup

### Prerequisites

- Python 3.13.3 or higher
- [uv](https://docs.astral.sh/uv/) for dependency management
- [just](https://github.com/casey/just) for task running (optional but recommended)

### Getting Started

1. **Clone the repository**
   ```bash
   git clone <repository-url>
   cd civic-observer
   ```

2. **Install dependencies**
   ```bash
   uv sync --group dev --group test
   # or with just
   just install
   ```

3. **Run database migrations**
   ```bash
   uv run python manage.py migrate
   # or with just
   just migrate
   ```

4. **Create a superuser (optional)**
   ```bash
   uv run python manage.py createsuperuser
   # or with just
   just createsuperuser
   ```

5. **Start the development server**

   For development with Tailwind CSS auto-compilation:
   ```bash
   uv run python manage.py tailwind runserver
   # or with just
   just dev
   ```

   Or run the standard Django development server (requires manual Tailwind builds):
   ```bash
   uv run python manage.py runserver
   # or with just
   just manage runserver
   ```

The application will be available at `http://localhost:8000`.

## Development Workflow

### Running Tests

Run the full test suite with coverage:
```bash
uv run pytest
# or with just
just test
```

Run tests with coverage report:
```bash
uv run pytest --cov
# or with just
just manage pytest --cov
```

Run tests in fast mode (stop on first failure):
```bash
uv run pytest -x --ff
# or with just
just manage pytest -x --ff
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
# or with just
just check
```

### Static Files and Tailwind CSS

Static files are served using WhiteNoise.

**Tailwind CSS Development:**
```bash
# Watch for changes and auto-compile CSS during development
uv run python manage.py tailwind watch
# or with just
just watch-css

# Build optimized CSS for production
uv run python manage.py tailwind build
# or with just
just build-css
```

**Collect static files:**
```bash
uv run python manage.py collectstatic
# or with just
just collectstatic
```

## Configuration

The project uses environment-based settings:
- `config.settings.development` - Development (default)
- `config.settings.production` - Production

Set the `DJANGO_SETTINGS_MODULE` environment variable to switch between configurations.

## Just Commands

This project uses [just](https://github.com/casey/just) for task running. Available commands:

- `just dev` - Start development server with Tailwind CSS auto-compilation
- `just test` - Run the test suite
- `just check` - Run all code quality checks (tests, linting, type checking)
- `just manage <args>` - Django management command passthrough
- `just install` - Install dependencies
- `just migrate` - Run database migrations
- `just makemigrations` - Create new migrations
- `just createsuperuser` - Create superuser
- `just build-css` - Build Tailwind CSS for production
- `just watch-css` - Watch Tailwind CSS for changes
- `just collectstatic` - Collect static files
- `just list` - Show all available commands
