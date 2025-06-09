# ------------------------------------------------------------
# Base/builder layer
# ------------------------------------------------------------

FROM python:3.13-slim-bookworm AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH=/srv
ENV PYTHONUNBUFFERED=1

COPY pyproject.toml /tmp/pyproject.toml

# add ",sharing=locked" if release should block until builder is complete
RUN --mount=type=cache,target=/root/.cache,sharing=locked,id=pip \
    python -m pip install --upgrade pip uv just-bin

RUN --mount=type=cache,target=/root/.cache,sharing=locked,id=pip \
    python -m uv pip compile /tmp/pyproject.toml -o /tmp/requirements.txt

RUN --mount=type=cache,target=/root/.cache,sharing=locked,id=pip \
    python -m uv pip install --system --requirement /tmp/requirements.txt

# ------------------------------------------------------------
# Dev/testing layer
# ------------------------------------------------------------

FROM builder AS release

COPY . /src/

WORKDIR /src/

CMD ["uvicorn", "config.asgi:application", "--host", "0.0.0.0", "--limit-max-requests", "100", "--timeout-graceful-shutdown", "7"]

# ------------------------------------------------------------
# TODO: Add Production notes
# ------------------------------------------------------------
