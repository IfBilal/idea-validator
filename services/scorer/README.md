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

## Local-first mode (P8-T07, docs/harness/11 §4)
Set `LOCAL_LLM_URL` (the llama.cpp server holding the fine-tuned GGUF, same pod, loopback). `POST /score` then:
local model with JSON-schema constrained decoding -> validate (JSON, ranges, evidence ids subset of the request) ->
invalid: retry once -> still invalid, or the server is down (no retry): hosted model via M5 (`LLM_URL`), logged and counted
in `scorer_fallback_total{reason="invalid_output"|"unavailable"}`, result `fallback: true`, `model` = hosted model ->
still invalid: abstain (`score: null`, `confidence: 0`). A local answer carries `model: scorer-lora@v1`
(`LOCAL_MODEL_NAME`). Hosted transport errors still propagate (M9 turns them into abstentions). With only `LOCAL_LLM_URL`
set the service has no fallback and abstains on invalid output.
`/ready` is true only when the model has loaded (`/health`) and one warm-up completion succeeded.
Config: `LOCAL_LLM_URL`, `LOCAL_MODEL_NAME` (`scorer-lora@v1`), `LOCAL_TIMEOUT_S` (120).

Deployment shape (for the Kubernetes generator): the scorer pod runs two containers, `llama-server` (pinned llama.cpp
server image, `-m /models/scorer-lora-v1.gguf -c 4096 -np 1 --host 127.0.0.1`, model on the `models` PVC, filled by the
`model-import` Job through M5 and verified against the release sha256) and this wrapper. Measured on a Ryzen 5 5600
(4 threads, 570-token prompt, Q4_K_M): p50 3.6 s, p95 5.5 s, peak RSS 1.86 GB (`jobs/finetune/runs/smoke_serving.json`,
synthetic weights: re-measure with the real model). Start from requests cpu 1 / mem 2Gi, limits cpu 2 / mem 3Gi.
