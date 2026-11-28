# Build from the repository root:  docker build -f jobs/evaluation/Dockerfile -t evaluation:<git-sha> .
# Guide §4.4 pattern: multi-stage, non-root (UID 10001), pinned base, no `latest`.
FROM python:3.12.16-slim-bookworm AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.9 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY libs/common ./libs/common
# evaluation depends on the pure M11 engine (offline tuning); the runner itself never imports it
COPY services/verdict ./services/verdict
COPY jobs/evaluation ./jobs/evaluation
RUN uv sync --frozen --no-dev --no-editable --package idea-validator-evaluation

FROM python:3.12.16-slim-bookworm
RUN useradd --uid 10001 --user-group --no-create-home --shell /usr/sbin/nologin app
WORKDIR /app
COPY --from=build --chown=10001:10001 /app/.venv /app/.venv
# the labelled set (when it exists) and the engine config are the only data the Job reads;
# tests are excluded by .dockerignore
COPY --chown=10001:10001 eval/labelled /app/eval/labelled
COPY --chown=10001:10001 config /app/config
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER 10001
ENTRYPOINT ["python", "-m", "evaluation.run"]
CMD ["--labelled", "/app/eval/labelled/labelled_set.jsonl", "--out", "/tmp/eval-results"]
