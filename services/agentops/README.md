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

## Registry (P9-T03)
`GET /registry` lists every prompt (`prompts/<agent>@vN.md`), rubric (`rubrics/<dim>@vN.yaml`), config version
(`verdict.engine|weights|thresholds|rubric_set`, `budgets`, `prompts`, `llm`) and model alias with a sha256 content hash
(never the content), the active prompt and rubric versions, and `in_sync` / `problems` against `registry.lock.json`.
The files are read from `REGISTRY_DIR` (default `/app/registry`, copied into the image by the Dockerfile).
`registry.lock.json` (repo root) maps `kind:name@version -> sha256`. `python scripts/check_registry.py` (CI job `registry`,
stdlib + PyYAML) exits 1 when a prompt/rubric file was edited in place, a version was deleted or renamed, a config
changed without bumping its version key, or a new version is not yet registered. A new version: add the file, run
`python scripts/check_registry.py --update` (it only adds entries and refuses when an old one changed), commit the lock.
Not enforced yet: the "a version bump needs a new `eval/results/<date>-<version>.json`" half of docs/harness/12 section 4.

## Drift (P9-T05)
`python -m agentops.drift [--url U | --trace-dir D] [--kb-url K | --stats-file F] [--snapshot S.jsonl.gz] [--out-dir D] [--post] [--strict]`
computes: freshness (age of the newest card per source; stale after 30 days), coverage (cards per source and month from a
snapshot, or growth against the previous report when only M6 `/stats` is available; sources with no new cards in the
window), and the verdict distribution shift (last N runs against the N before, total variation distance, flagged when
`> 0.2` and both sides have at least 5 runs). Verdicts are read from the `m11_verdict` traces, never recomputed.
`--post` stores the report through `POST /drift`; `GET /drift/latest` returns the newest (`DRIFT_DIR`, default
`<TRACE_DIR>/drift`, last 60 kept). The CronJob `drift-report` (daily 05:20, generated by `scripts/gen_k8s.py`) reaches only
agentops and kb.

## Metrics and dashboard (P9-T04)
Series derived from newly stored traces (retries are not double counted): `agent_step_latency_seconds{agent,kind}`,
`llm_tokens_total{agent,model,direction}`, `trace_cost_usd_total{agent}`, `cache_requests_total{cache,result}` (cache = the
record kind), `scorer_fallback_total{agent}`, `trace_errors_total{agent}`, `verdicts_total{verdict}`.
`k8s/dashboards/idea-validator.json` (import into Grafana) has the six required panels. Two have no source yet:
**Blocked injections** needs M5 (tools) to emit `blocked_injections_total{source}`, **Budget stops** needs M3 (orchestrator)
to emit `runs_total{kind,status}` with `status="budget_stopped"`. Prometheus scraping and the Grafana deployment are not part
of the manifests yet.
## Safety properties (security review of PR #94)
- **Bounded work.** Every string is clipped to 4096 chars (and containers to 200 items, depth 8) before any
  regex runs; redaction patterns use bounded quantifiers (`common.redaction`), so cost is linear. An error
  text is cut to 2000 chars first. Sanitising runs in a worker thread, off the event loop. A shared
  positive/negative corpus (`common.testing.REDACTION_POSITIVE/NEGATIVE`) pins what is and is not redacted.
- **File first, then index.** `POST /traces` appends to the run's JSONL first (the source of truth), then inserts
  the index rows; nothing is ever deleted on failure. A failed write is cut back under the same `flock`, a torn
  last line is closed with a newline before the next append, readers de-duplicate by record id. A crash between
  the two steps leaves a line without an index row: `python -m agentops.reindex` (needs `DATABASE_URL`,
  `TRACE_DIR`) rebuilds missing rows and run owners from the files. Idempotent.
- **Growth.** A run holds at most `MAX_RUN_RECORDS` (5000) records and `MAX_RUN_BYTES` (8 MiB), enforced under the
  lock (413-style per-record rejection). Below `MIN_FREE_BYTES` (64 MiB) free, ingest answers 507 and `/ready`
  fails. `python -m agentops.retention --days N [--dry-run]` deletes runs older than N days (file and index);
  the CronJob `agentops-retention` runs it daily at 03:30 for 14 days and is scheduled beside the agentops pod
  because the PVC is ReadWriteOnce. PVC size: a run is ~100 KB, 1 Gi holds thousands of runs, 14 days of
  retention keeps it well below the free-space guard.
- **Ownership.** The orchestrator traces a `run_owner` decision (`user_id`) at run start; M13 stores it in
  `ops.run_owners` (first owner wins). `GET /runs?user_id=` lists only that user's runs and
  `GET /runs/{id}/trace?user_id=` answers 404 (same as unknown) for another user's or an unowned run.
  M13 trusts its caller: **the gateway must verify ownership via M2, pass the verified `user_id`, and must
  never proxy `/runs` or `/traces` to a browser.**
- **Trusted decisions.** `m11_verdict` and `run_owner` decisions are accepted only from service `orchestrator`
  (others are rejected); replay only reads those. The `m11_verdict` input is kept up to 32 KB (the engine
  input is numbers and ids); everything else stays at 4 KB.
- **What traces hold.** Step outputs are summarised (field names, counts, numbers, ids, hash), never text; error
  text is the type plus a capped, redacted message for our own errors, and only the type for others.
- **Replay proves M11 determinism for the recorded input** (same engine, same rules, same numbers give the same
  verdict). It does not prove what the user was shown. A rules-version change fails unless
  `--allow-version-drift`; a truncated trace or truncated input is exit 2; the report prints how many M11
  steps were replayed.
