# validators (M9): Validation agents

Part of the Idea Validator system (docs/harness/03_ARCHITECTURE.md). One container per module.

| | |
|---|---|
| Module | M9 |
| Port | 8000 |
| Probes | `GET /health` (liveness), `GET /ready` (dependency checks), `GET /metrics` (Prometheus) |
| Owner | see `PROGRESS.md` / `services/README.md` |

## Layout
`src/svc_validators/`: `main.py` (app), `config.py` (settings), `api/` (routes only), `services/` (business logic),
`repositories/` (DB/Redis access), `clients/` (typed clients for other modules), `models/`.
Layering: `api -> services -> repositories/clients`; routes never touch the DB; services never import FastAPI.

## Endpoints (internal, `X-Internal-Token` required)
`POST /score-all` body `{run_id, idea, profile?, evidence[], features}` returns
`{scores: [DimensionScore x6], abstained: [dimension], extra: {estimated_effort_hours, required_skills, risk_blocker,
duplicate_of}, errors: [{scorer, dimension?, error}]}`. `POST /score/{scorer}` runs one scorer
(`demand_pain`, `competition`, `feasibility`, `risk`). Probes: `/health`, `/ready` (rubrics valid and M10 `/ready`), `/metrics`.

Four scorers, six dimensions (ADR-004): Demand&Pain, Competition&Differentiation, Feasibility (also Learning), Risk.
They run in parallel (`asyncio.gather(return_exceptions=True)`) under a per-scorer timeout; a failed or slow scorer becomes
an abstention plus an `errors` entry and never cancels the others.
Per dimension: feature prior from the rubric (`feature_prior` rules, safe AST evaluator, no `eval`/`exec`) + M10 `/score`
-> `round_half(alpha*model + (1-alpha)*prior)` (ADR-006). No valid evidence (none in the request, none cited, or the model
abstained) means `score: null, confidence: 0`. Confidence is `model_confidence * min(1, valid_evidence/3)`. If a feature
needed by the prior is missing the prior is "unavailable" and the model score alone is used (noted in `reason`).
Rubrics are loaded strictly at the pinned versions; a bad rubric fails startup and `/ready`.

## Config (environment / ConfigMap)
`LOG_LEVEL` (INFO), `INTERNAL_TOKEN` (K8s Secret), `SCORER_URL` (M10 base URL), `SCORER_TIMEOUT_S` (60, per scorer),
`SCORER_MAX_RETRIES` (2), `RUBRICS_DIR` (`/app/rubrics`), `RUBRIC_VERSIONS` (JSON, default every dimension at 1),
`ALPHA_MODEL` (overrides `scoring_blend.alpha_model` in `<CONFIG_DIR>/verdict.yaml`; default 0.6), `CONFIG_DIR`.
No hard-coded hosts.

## Run and test
```bash
uv run --package idea-validator-validators uvicorn svc_validators.main:app --port 8000
uv run pytest services/validators
docker build -f services/validators/Dockerfile -t validators:$(git rev-parse --short=7 HEAD) .
```
