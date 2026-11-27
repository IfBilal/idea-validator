# scorer (M10): Scoring Model

Part of the Idea Validator system (docs/harness/03_ARCHITECTURE.md). One container per module.

| | |
|---|---|
| Module | M10 |
| Port | 8000 |
| Probes | `GET /health` (liveness), `GET /ready` (dependency checks), `GET /metrics` (Prometheus) |
| Owner | see `PROGRESS.md` / `services/README.md` |

## Layout
`src/svc_scorer/`: `main.py` (app), `config.py` (settings), `api/` (routes only), `services/` (business logic),
`repositories/` (DB/Redis access), `clients/` (typed clients for other modules), `models/`.
Layering: `api -> services -> repositories/clients`; routes never touch the DB; services never import FastAPI.

## Endpoints (internal, `X-Internal-Token` required)
`POST /score`: body `ScoreRequest` (`rubric_id`, `idea`, `profile?`, `evidence[]`, `features`, `run_id`), reply
`DimensionScore`. Hosted mode: builds the prompt from `prompts/scorer@v1.md` + rubric anchors (evidence is escaped and
wrapped in `<evidence id=...>`), calls M5 `POST /llm/v1/chat/completions`, validates the JSON reply. Invalid output is
retried once, then the reply is an abstention (`score: null`, `confidence: 0`, `fallback: true`, metric
`scorer_fallback_total{reason}`). Cited evidence ids not in the request are dropped; none left means abstain.
The model never returns a verdict; unknown keys in its reply are discarded. Transport failures from M5 are returned as
502/504 error envelopes (the M9 caller turns them into abstentions).
Probes: `/health`, `/ready` (M5 reachable), `/metrics`.

## Config (environment / ConfigMap)
`LOG_LEVEL` (default INFO), `INTERNAL_TOKEN` (K8s Secret), `LLM_URL` (M5 base URL, required), `LLM_TIMEOUT_S` (60),
`LLM_MAX_RETRIES` (2), `MODEL_NAME` (`hosted-default`, stored as `DimensionScore.model`), `RUBRICS_DIR`
(`/app/rubrics`), `PROMPTS_DIR` (`/app/prompts`). No hard-coded hosts.

## Run and test
```bash
uv run --package idea-validator-scorer uvicorn svc_scorer.main:app --port 8000
uv run pytest services/scorer
docker build -f services/scorer/Dockerfile -t scorer:$(git rev-parse --short=7 HEAD) .
```
