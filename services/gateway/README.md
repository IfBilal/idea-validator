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

## Public endpoints (so far)
| | |
|---|---|
| `POST /auth/register` | `{email, password}` (password 10 to 128 chars, not a common password) -> 201 `{user_id}`; 409 duplicate email |
| `POST /auth/login` | -> 200 `{access_token, token_type: "bearer", expires_in: 3600}`; one generic 401 for every failure |
| `GET /profile`, `PUT /profile` | `Authorization: Bearer <jwt>`; each PUT creates a new version; the user comes from the token, never from the body |

Runs, ideas and the SSE stream arrive with the orchestrator (Phase 5).

## Config (environment / ConfigMap / Secret)
| Variable | Meaning |
|---|---|
| `JWT_SECRET` | Secret, at least 32 characters; the gateway refuses to start without it |
| `PROFILE_URL` | M2 base URL (cluster DNS), e.g. `http://profile.idea-validator.svc.cluster.local:8000` |
| `INTERNAL_TOKEN` | Secret shared with M2 (`X-Internal-Token`) |
| `REDIS_URL` | Rate-limit counters, shared by all gateway replicas (Secret) |
| `UI_ORIGIN` | The only origin CORS allows |
| `TRUST_FORWARDED_FOR` | `true` only behind the ingress, so the real client IP is used for limits |
| `IP_PER_MIN` / `LOGIN_PER_MIN` / `REGISTER_PER_MIN` | Defaults 100 / 5 / 10 (login is also limited per email) |
| `BODY_LIMIT_BYTES` | Default 32768; larger bodies get 413 |
| `JWT_TTL_S`, `LOG_LEVEL` | Defaults 3600 and INFO |

Every response carries CSP, `X-Content-Type-Options`, `Referrer-Policy`, `Cache-Control: no-store`. Errors from M2 are translated so no internal host name ever reaches a browser.

## Run and test
```bash
uv run --package idea-validator-gateway uvicorn svc_gateway.main:app --port 8000
uv run pytest services/gateway
docker build -f services/gateway/Dockerfile -t gateway:$(git rev-parse --short=7 HEAD) .
```
