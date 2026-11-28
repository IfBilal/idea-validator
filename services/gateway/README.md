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

## Public endpoints
All need `Authorization: Bearer <jwt>` except `/auth/*`. The user is always the token's `sub`; bodies are
validated with unknown fields refused. Errors use the standard envelope (`{"error":{code,message,details,request_id}}`).
| | |
|---|---|
| `POST /auth/register`, `POST /auth/login` | see below |
| `GET /profile`, `PUT /profile` | the profile (each PUT is a new version) |
| `POST /validate` | `{idea_text (<=4000), title (<=200, required for a new idea), idea_id|null}` -> 202 `{run_id, idea_id, version}`. New idea: created in M2. Existing `idea_id`: a new version unless the text equals the latest version (then that version is reused) |
| `POST /discover` | `{niche|null, count 1..10 (default 5), deep_search (default false)}` -> 202 `{run_id}` |
| `GET /runs/{id}` | `{run_id, kind, state, progress, spent, budget, pending_question, pending_approval, result, error}`; `result` is M3's card verbatim |
| `POST /runs/{id}/answer` | `{question_id, answer}` (a number, or a numeric string of at most 1000 characters; M3 asks numeric questions) -> 202 `{run_id, state}` |
| `POST /runs/{id}/approve` | `{approval_id, decision: approve|decline}` -> 202 |
| `GET /runs/{id}/trace?flat=` | the recorded trace of the caller's own run, proxied from M13 `GET /runs/{id}/trace` (ordered tree, `totals`, `by_agent`; `flat=true` for the raw ordered list). M2 confirms ownership first, so an unknown and a foreign run both give the same 404 and never reach M13. Answers larger than `MAX_TRACE_BYTES` or naming another run are refused (502, generic); M13 failures are generic 502/504; 503 when `AGENTOPS_URL` is unset. Own limit `USER_TRACE_PER_MIN` (default 30). M13's `GET /runs` (every user's runs) is never proxied |
| `GET /runs/{id}/events` | SSE, see below |
| `GET /ideas?limit=1..50&cursor=` | `{items:[{idea_id,title,source,latest_version,latest_verdict,score,updated_at}], next_cursor}` |
| `GET /ideas/{id}/versions`, `GET /ideas/{id}/versions/{v}` | history; the single version has `card` (the stored verdict card, `null` until validated) |
| `GET /compare?ids=a,b,c` | 2 to 5 different idea ids -> `{ideas:[row], dimensions:[{dimension, scores:{idea_id:score|null}, delta}], verdicts:{idea_id:label|null}}` for the latest version of each. `delta` = highest minus lowest known score (presentation only) |
| `POST /ideas/{id}/challenge` | `{version, claim_id}` -> 202 `{run_id}` (404 if the claim is not in the stored card, 409 if the version has no verdict) |
| `POST /ideas/{id}/whatif` | `{version, change_id | custom_change (<=1000)}` (exactly one) -> 202 `{run_id}` |

**Deviation from docs/harness/05:** `/compare` takes `ids=a,b,c` (2..5) instead of `a=` and `b=` (no `:v`; M2 compares latest versions), and returns a list-based shape. `GET /ideas/{id}/versions/{v}` names the stored card `card`.

**Ownership.** A foreign or unknown idea/run is always 404 with the same message; M2 enforces it (every read carries the token's user id) and the gateway records each run's owner in M2 (`PUT /internal/runs/{id}`) right after M3 starts it. Compare is all-or-nothing: one foreign id makes the whole request a 404.

**Verdicts.** The gateway never computes or edits one. `result`/`card` are forwarded unchanged. A card with `verdict: null` and `partial: true` (M3's null-verdict contract: `needs_input`, `missing`, `stop_reason`, `error`) has no verdict even when the run state is `succeeded`; the UI must show "needs your input" or "stopped", never GO/PIVOT/NO-GO. `question` events and `pending_question` carry `field`, `required` and `range` as M3 sends them.

### Event stream (SSE)
`GET /runs/{id}/events` returns `text/event-stream`. Frames: `event: <type>`, `id: <seq>`, `data: <RunEvent JSON>`. Each event is validated as a `RunEvent` of that run; anything else is dropped.
- **Auth with `fetch`, not `EventSource`:** `EventSource` cannot send an `Authorization` header, so the UI reads the stream with `fetch(url, {headers:{Authorization, "Last-Event-ID": lastSeq}})` and parses frames from `response.body.getReader()`. On disconnect it reconnects with the last `id` it saw.
- **Resume:** `Last-Event-ID: n` replays events with `seq > n`, then goes live. A malformed or negative value means "from the start".
- **Heartbeat** every 15 s (`event: heartbeat`, no `id`, so `Last-Event-ID` never moves on it).
- **Closing:** after `run_completed`, `run_failed` or `budget_stopped`; after `SSE_MAX_S` (default 900 s, reconnect to continue); when the access token expires (a stream never outlives the JWT it was opened with); or when the client goes away (the Redis subscription and the stream slots are always released).
- **Limits:** at most `SSE_STREAMS_PER_USER` (default 3, counted in Redis with a TTL) open streams per user and `SSE_STREAMS_GLOBAL` (default 100) per replica; over the user cap is 429 with `Retry-After`, over the replica cap is 503. Pub/sub uses its own bounded Redis pool (`SSE_POOL_SIZE`), separate from the rate limiter. A frame over `SSE_MAX_FRAME_BYTES` (64 KB) keeps its type and `id` but its payload becomes `{"truncated": true}`. Failure events carry a fixed `reason` text, never M3's own. For a run that already finished the stored events are replayed; if none are stored, one synthetic closing event (`payload.synthesised: true`) with the next `seq` is sent.
- **Source:** Redis pub/sub `run:{run_id}` (taken before replay, so nothing is lost; duplicates and old seqs are dropped; a gap in the live sequence is filled from storage).
- **Assumption about M3:** stored events are read from `GET /runs/{id}/events?after=<seq>` -> `{"events":[RunEvent,...]}`. The orchestrator offers it (it keeps events in Redis for 7 days); a 404 or error there only means "no stored events" and the stream is live-only. Run kinds `discover`, `challenge`, `whatif` are sent to M3 `POST /runs` as `{kind, user_id, input}`; M3 currently accepts only `validate` and will answer 422 (surfaced as 422) until those flows land.

### Starting runs safely (idempotency)
`POST /validate`, `/discover`, `/ideas/{id}/challenge` and `/whatif` accept an optional `Idempotency-Key` header (1 to 64 of `A-Za-z0-9_.:-`). A repeat of the same request (same user, kind and body; same key if given) returns the run it already started instead of a second one: 60 s window without the header, 10 min with it. A new idea or version is remembered under the same key, so a start that failed (M3 down) is retried on the same idea, never on a new draft. A run that ended `failed` or `budget_stopped` is not reused. If the owner record cannot be written to M2 after M3 started the run, the gateway retries the write and then calls M3 `POST /runs/{id}/cancel`; `409` means the same request is still being processed. M3 chooses the run id, so the owner record is written right after the start (not before).

### What M3 may tell a browser
`progress` (`step`, `round`, `percent`), `spent` and `budget` (numeric counters and limits) are rebuilt from an allow-list; `error` is a fixed text chosen by the run state. A run M2 records as running but M3 no longer knows is reported `failed` and M2 is corrected. Every M3 call after the start carries `X-User-Id`.

### Rate limits
Per IP on every route (`IP_PER_MIN`, default 100). Per user: every authenticated request (`USER_PER_MIN`, default 120); starting runs (`validate`, `discover`, `challenge`, `whatif` share `USER_RUNS_PER_MIN`, default 10) and new event streams (`SSE_PER_MIN`, default 30). Over the limit: 429 with `Retry-After`; Redis down: 503.

## Config (environment / ConfigMap / Secret)
| Variable | Meaning |
|---|---|
| `JWT_SECRET` | Secret, at least 32 characters; the gateway refuses to start without it |
| `ORCHESTRATOR_URL` | M3 base URL; the gateway also needs `/ready` of M3 for its own `/ready` |
| `USER_RUNS_PER_MIN` / `USER_PER_MIN` / `SSE_PER_MIN` / `SSE_HEARTBEAT_S` / `SSE_MAX_S` | Defaults 10 / 120 / 30 / 15 / 900 |
| `SSE_STREAMS_PER_USER` / `SSE_STREAMS_GLOBAL` / `SSE_POOL_SIZE` / `SSE_MAX_FRAME_BYTES` | Defaults 3 / 100 / 110 / 65536 |
| `AGENTOPS_URL` / `MAX_TRACE_BYTES` / `USER_TRACE_PER_MIN` | M13 base URL (optional: unset means the trace route answers 503), response cap (default 1 MiB), per-user trace limit (default 30). The gateway's `/ready` does not check M13: tracing being down must not take the API out of rotation |
| `PROFILE_URL` | M2 base URL (cluster DNS), e.g. `http://profile.idea-validator.svc.cluster.local:8000` |
| `INTERNAL_TOKEN` | Secret shared with M2 (`X-Internal-Token`) |
| `REDIS_URL` | Rate-limit counters and the `run:{id}` event channels (Secret) |
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
- **Deployment notes for the Kubernetes manifests:** the dev overlay ConfigMap must set `TRUSTED_PROXY_HOPS=1` (with 0 every user behind the ingress shares one IP limit bucket); keep `/metrics` and `/ready` off the Ingress path (only `/api` should route here); API docs are disabled in every service by default.
