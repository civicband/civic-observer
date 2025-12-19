# ------------------------------------------------------------
# Stage 1: Builder - Install dependencies
# ------------------------------------------------------------

FROM python:3.13-slim-bookworm AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY pyproject.toml /tmp/pyproject.toml

RUN --mount=type=cache,target=/root/.cache,sharing=locked,id=pip \
    python -m pip install --upgrade pip uv

RUN --mount=type=cache,target=/root/.cache,sharing=locked,id=pip \
    python -m uv pip compile /tmp/pyproject.toml -o /tmp/requirements.txt

RUN --mount=type=cache,target=/root/.cache,sharing=locked,id=pip \
    python -m uv pip install --system --requirement /tmp/requirements.txt

# ------------------------------------------------------------
# Stage 2: Asset Builder - Build static assets
# ------------------------------------------------------------

FROM builder AS asset-builder

# Install Node.js for Tailwind
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

COPY . /src/
WORKDIR /src/

# Build Tailwind CSS and collect static files
RUN python manage.py tailwind build
RUN python manage.py collectstatic --noinput --skip-checks

# ------------------------------------------------------------
# Stage 3: Release - Final production image
# ------------------------------------------------------------

FROM python:3.13-slim-bookworm AS release

ENV PIP_DISABLE_PIP_VERSION_CHECK=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH=/src
ENV PYTHONUNBUFFERED=1

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user with UID/GID 1000
RUN groupadd --gid 1000 app && \
    useradd --uid 1000 --gid 1000 --create-home --shell /bin/bash app

# Copy Python packages from builder
COPY --from=builder /usr/local/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application code
COPY --chown=app:app . /src/

# Copy pre-built static files from asset-builder
COPY --from=asset-builder --chown=app:app /src/staticfiles /src/staticfiles
COPY --from=asset-builder --chown=app:app /src/theme/static /src/theme/static

WORKDIR /src/

# Switch to non-root user
USER app

CMD ["uvicorn", "config.asgi:application", "--host", "0.0.0.0", "--limit-max-requests", "100", "--timeout-graceful-shutdown", "7"]
