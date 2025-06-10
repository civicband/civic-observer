# CivicObserver Development Commands

# Start development server with Tailwind CSS auto-compilation
dev:
    uv run python manage.py tailwind runserver

# Run the test suite
test *args:
    uv run --group test pytest {{args}}

# Run all code quality checks (linting, type checking, tests)
check:
    uv run --group test pytest
    uv run --group dev ruff check .
    uv run --group dev mypy .

# Run type checking (mypy) for type safety and correctness of code
mypy:
    uv run --group dev mypy .

# Run linting (ruff) for consistent code formatting and style
ruff:
    uv run --group dev ruff check .

# Django management command passthrough
manage *args:
    uv run python manage.py {{args}}

# Build Tailwind CSS for production
build-css:
    uv run python manage.py tailwind build

# Watch Tailwind CSS for changes
watch-css:
    uv run python manage.py tailwind watch

# Collect static files
collectstatic:
    uv run python manage.py collectstatic --noinput

# Run database migrations
migrate:
    uv run python manage.py migrate

# Create new migrations
makemigrations:
    uv run python manage.py makemigrations

# Create superuser
createsuperuser:
    uv run python manage.py createsuperuser

# Install dependencies
install:
    uv sync --group dev --group test
