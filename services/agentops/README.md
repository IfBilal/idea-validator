# agentops (M13): AgenticOps

Part of the Idea Validator system (docs/harness/03_ARCHITECTURE.md). One container per module.

| | |
|---|---|
| Module | M13 |
| Port | 8000 |
| Probes | `GET /health` (liveness), `GET /ready` (dependency checks), `GET /metrics` (Prometheus) |
| Owner | see `PROGRESS.md` / `services/README.md` |

## Layout
`src/svc_agentops/`: `main.py` (app), `config.py` (settings), `api/` (routes only), `services/` (business logic),
`repositories/` (DB/Redis access), `clients/` (typed clients for other modules), `models/`.
Layering: `api -> services -> repositories/clients`; routes never touch the DB; services never import FastAPI.

## Config (environment / ConfigMap)
`LOG_LEVEL` (default INFO), `INTERNAL_TOKEN` and `DATABASE_URL` (from a K8s Secret), `TRACE_DIR` (default `/var/lib/agentops/traces`, a PVC in K8s). Optional limits: `MAX_BATCH` (100), `MAX_BODY_BYTES` (2 MiB), `MAX_RECORD_BYTES` (16 KiB), `MAX_RUN_RECORDS` (5000).
No hard-coded hosts: other modules are reached through `*_URL` environment variables.

## Run and test
```bash
uv run --package idea-validator-agentops uvicorn svc_agentops.main:app --port 8000
uv run pytest services/agentops
docker build -f services/agentops/Dockerfile -t agentops:$(git rev-parse --short=7 HEAD) .
```

## Trace store (P9-T01)
Every service ships its `TraceRecord`s here through `common.trace_sink.AgentOpsSink` (set `AGENTOPS_URL`; the sink
batches up to 100 records, keeps a bounded queue and drops-and-counts on overflow, so tracing never slows or breaks a run).

| Endpoint | Purpose |
|---|---|
| `POST /traces` | one record or a list of at most 100. Each record is validated (Pydantic model that mirrors `schemas/trace_record.schema.json`), redacted again (secrets, e-mails, phone numbers, JWTs), payloads re-capped at 4 KB, and rejected over 16 KB. Answer `{accepted, duplicates, rejected:[{index, reason}]}`: a bad record never blocks the good ones. Idempotent: the record id is a hash of the cleaned record, so a retried batch stores nothing twice. |
| `GET /runs/{run_id}/trace` | ordered tree of steps (`parent_step`), `totals` and `by_agent` (steps, tokens, cost, latency, errors). `?flat=true` returns the raw ordered list. 404 for an unknown run, 422 for an invalid id. |
| `GET /runs?limit=&after=` | runs newest first (1-100, default 20) with an opaque `next` cursor. |

All need `X-Internal-Token`. The gateway checks run ownership with M2 first and passes through.

Storage: `ops.traces_index` (Postgres, Alembic `svc_agentops:migrations`, Job `migrate-agentops`) holds one row per record
(run_id, record_id, ts, kind, agent, step, parent, status, cost, tokens, latency). The full records are JSONL under
`TRACE_DIR/<run_id>.jsonl`: `flock` + one `O_APPEND` write + fsync per batch, file names only from run ids matching
`[A-Za-z0-9_-]{1,64}`, symlinks never followed. If the file append fails the index rows are removed and the caller gets
a 500, so a retry is safe. `step_id` counts per service process, so the tree resolves a parent among records of the same
service; siblings are ordered by estimated start (`ts - latency`) because a span is emitted when it ends.

## Replay (P9-T02)
`make replay RUN=<run_id>` (locally `TRACE_DIR=<dir> make replay RUN=...`, otherwise `kubectl exec deploy/agentops`) runs
`python -m agentops.replay <run_id> [--trace-dir D | --url U] [--config-dir C]`. The orchestrator records, next to each M11
call, a `decision` trace named `m11_verdict` whose input holds the engine inputs (numbers and evidence ids only, no scorer
free text) and whose output holds M11's answer. Replay re-runs the pure engine (`svc_verdict`) in-process on it and
compares verdict, stability, score, confidence and gates. Exit 0 reproduced, 1 mismatch (diff printed), 2 nothing to replay
(no such step, truncated input, unreadable trace). A different rules version is reported as a note.
