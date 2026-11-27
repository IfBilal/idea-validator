# Build from the repository root:  docker build -f services/validators/Dockerfile -t validators:<git-sha> .
# Guide §4.4 pattern: multi-stage, non-root (UID 10001), pinned base, no `latest`.
FROM python:3.12.13-slim-bookworm AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.9 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY libs/common ./libs/common
COPY services/validators ./services/validators
RUN uv sync --frozen --no-dev --no-editable --package idea-validator-validators

FROM python:3.12.13-slim-bookworm
# Debian base lags security fixes (perl-base CVE-2026-13221/42496/8376 fixed in deb12u4): pull only that fix.
RUN apt-get update && apt-get install -y --no-install-recommends --only-upgrade perl-base && rm -rf /var/lib/apt/lists/*
RUN useradd --uid 10001 --user-group --no-create-home --shell /usr/sbin/nologin app
WORKDIR /app
COPY --from=build --chown=10001:10001 /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER 10001
EXPOSE 8000
CMD ["uvicorn", "svc_validators.main:app", "--host", "0.0.0.0", "--port", "8000"]
