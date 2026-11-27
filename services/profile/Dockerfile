# Build from the repository root:  docker build -f services/profile/Dockerfile -t profile:<git-sha> .
# Guide §4.4 pattern: multi-stage, non-root (UID 10001), pinned base, no `latest`.
FROM python:3.12.16-slim-bookworm AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.9 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY libs/common ./libs/common
COPY services/profile ./services/profile
RUN uv sync --frozen --no-dev --no-editable --package idea-validator-profile

FROM python:3.12.16-slim-bookworm
RUN useradd --uid 10001 --user-group --no-create-home --shell /usr/sbin/nologin app
WORKDIR /app
COPY --from=build --chown=10001:10001 /app/.venv /app/.venv
# the migrate Job (k8s/base/jobs/migrate.yaml) runs `alembic -c /app/alembic.ini upgrade head` from this image
COPY --from=build --chown=10001:10001 /app/services/profile/alembic.ini /app/alembic.ini
COPY --from=build --chown=10001:10001 /app/services/profile/migrations /app/migrations
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER 10001
EXPOSE 8000
CMD ["uvicorn", "svc_profile.main:app", "--host", "0.0.0.0", "--port", "8000"]
