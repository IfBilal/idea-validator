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
| `UI_ORIGIN` | The only origin CORS allows; one `scheme://host[:port]`, never `*` |
| `TRUSTED_PROXY_HOPS` | `1` behind the ingress, `0` (default) when reached directly. The client IP for rate limits is the `X-Forwarded-For` entry that many places from the right; invalid values fall back to the TCP peer. **The ingress must overwrite the header** (ingress-nginx default), or the limits can be dodged |
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

## Behaviour worth knowing
- **Rate limits** are fixed windows of one minute, shared by all replicas through Redis. Login attempts count per IP; only *failed* logins count per email (hashed), so successful logins never lock an account out of its budget. The 15-minute account lockout after 5 failures is enforced by M2. If Redis is down the gateway fails closed with 503.
- **Registration** answers 409 for a taken email (an account-enumeration trade-off the API contract accepts; the limiter bounds it).
- **Tokens** are HS256 with `iss`, `aud`, `iat`, `exp`, `sub` (a UUID); expiry is 60 minutes and there is no revocation.
- **Deployment notes for the Kubernetes manifests:** set `TRUSTED_PROXY_HOPS=1`; keep `/metrics` and `/ready` off the Ingress path (only `/api` should route here); API docs are disabled in every service by default.
