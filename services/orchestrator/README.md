# orchestrator (M3): Orchestrator

Part of the Idea Validator system (docs/harness/03_ARCHITECTURE.md). One container per module.

| | |
|---|---|
| Module | M3 |
| Port | 8000 |
| Probes | `GET /health` (liveness), `GET /ready` (dependency checks), `GET /metrics` (Prometheus) |
| Owner | see `PROGRESS.md` / `services/README.md` |

## Layout
`src/svc_orchestrator/`: `main.py` (app), `config.py` (settings), `api/` (routes only), `services/` (business logic),
`repositories/` (DB/Redis access), `clients/` (typed clients for other modules), `models/`.
Layering: `api -> services -> repositories/clients`; routes never touch the DB; services never import FastAPI.

## Config (environment / ConfigMap)
`LOG_LEVEL` (default INFO), `INTERNAL_TOKEN` (from a K8s Secret). Service-specific variables are added in `config.py`.
No hard-coded hosts: other modules are reached through `*_URL` environment variables.

## Run and test
```bash
uv run --package idea-validator-orchestrator uvicorn svc_orchestrator.main:app --port 8000
uv run pytest services/orchestrator
docker build -f services/orchestrator/Dockerfile -t orchestrator:$(git rev-parse --short=7 HEAD) .
```
