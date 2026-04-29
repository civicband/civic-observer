# CivicObserver Development Commands

# Start all services (web, worker, db, redis)
dev:
    docker compose up

# Start development server with Tailwind CSS auto-compilation
dev-tailwind:
    docker compose run --rm --service-ports web python manage.py tailwind runserver 0.0.0.0:8000

# Run the test suite (runs locally, connects to Docker PostgreSQL on port 5433)
test *args:
    uv run --group test pytest {{args}}

# Run all code quality checks (linting, type checking, tests)
check:
    uv run --group test pytest
    uv run --group dev ruff check . --fix
    uv run --group dev mypy .

# Run type checking (mypy) for type safety and correctness of code
mypy:
    uv run --group dev mypy .

# Run linting (ruff) for consistent code formatting and style
ruff:
    uv run --group dev ruff check . --fix

# Django management command passthrough (runs in Docker)
manage *args:
    docker compose run --rm utility python manage.py {{args}}

# Build Tailwind CSS for production
build-css:
    docker compose run --rm utility python manage.py tailwind build

# Watch Tailwind CSS for changes
watch-css:
    docker compose run --rm utility python manage.py tailwind watch

# Collect static files
collectstatic:
    docker compose run --rm utility python manage.py collectstatic --noinput

# Run database migrations
migrate:
    docker compose run --rm utility python manage.py migrate

# Create new migrations
makemigrations:
    docker compose run --rm utility python manage.py makemigrations

# Create superuser
createsuperuser:
    docker compose run --rm utility python manage.py createsuperuser

# Access PostgreSQL shell
dbshell:
    docker compose exec db psql -U postgres

# Stop all services
stop:
    docker compose stop

# Stop and remove all containers
down:
    docker compose down

# View logs for all services
logs:
    docker compose logs -f

# View logs for specific service
logs-service service:
    docker compose logs -f {{service}}

# Install dependencies (for local development tools)
install:
    uv sync --group dev --group test

# Quickwit: Create index on Quickwit server
quickwit-create-index:
    docker compose exec quickwit quickwit index create --index-config /opt/quickwit/index-config.yaml

# Quickwit: Index meeting pages from PostgreSQL
quickwit-index *args:
    docker compose run --rm utility python manage.py index_meeting_pages_quickwit {{args}}

# Quickwit: Search index (curl shortcut)
quickwit-search query="" limit="10":
    @curl -s "http://localhost:7280/api/v1/_elastic/meeting_pages/_search" \
      -H "Content-Type: application/json" \
      -d "{\"query\":{\"match_all\":{}},\"from\":0,\"size\":{{limit}},\"sort\":[{\"meeting_date\":\"desc\"}]}" | python3 -m json.tool

# Quickwit: Show index stats
quickwit-stats:
    @curl -s "http://localhost:7280/api/v1/indexes/meeting_pages" | python3 -m json.tool
