# Build from the repository root:  docker build -f jobs/ingestion/Dockerfile -t ingestion:<git-sha> .
# Guide §4.4 pattern: multi-stage, non-root (UID 10001), pinned base, no `latest`.
FROM python:3.12.16-slim-bookworm AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.9 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY libs/common ./libs/common
COPY jobs/ingestion ./jobs/ingestion
RUN uv sync --frozen --no-dev --no-editable --package idea-validator-ingestion

FROM python:3.12.16-slim-bookworm
RUN useradd --uid 10001 --user-group --no-create-home --shell /usr/sbin/nologin app
WORKDIR /app
COPY --from=build --chown=10001:10001 /app/.venv /app/.venv
# the seed Job restores the newest snapshot from here (data/snapshots is the one data dir kept
# in the build context, see .dockerignore)
COPY --chown=10001:10001 data/snapshots /app/data/snapshots
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    INGESTION_STATE_DIR=/tmp/ingestion
USER 10001
ENTRYPOINT ["python", "-m", "ingestion"]
CMD ["restore", "/app/data/snapshots"]
