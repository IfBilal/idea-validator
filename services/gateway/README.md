# gateway (M1): API Gateway

Part of the Idea Validator system (docs/harness/03_ARCHITECTURE.md). One container per module.

| | |
|---|---|
| Module | M1 |
| Port | 8000 |
| Probes | `GET /health` (liveness), `GET /ready` (dependency checks), `GET /metrics` (Prometheus) |
| Owner | see `PROGRESS.md` / `services/README.md` |

## Layout
`src/svc_gateway/`: `main.py` (app), `config.py` (settings), `api/` (routes only), `services/` (business logic),
`repositories/` (DB/Redis access), `clients/` (typed clients for other modules), `models/`.
Layering: `api -> services -> repositories/clients`; routes never touch the DB; services never import FastAPI.

## Config (environment / ConfigMap)
`LOG_LEVEL` (default INFO), `INTERNAL_TOKEN` (from a K8s Secret). Service-specific variables are added in `config.py`.
No hard-coded hosts: other modules are reached through `*_URL` environment variables.

## Run and test
```bash
uv run --package idea-validator-gateway uvicorn svc_gateway.main:app --port 8000
uv run pytest services/gateway
docker build -f services/gateway/Dockerfile -t gateway:$(git rev-parse --short=7 HEAD) .
```
