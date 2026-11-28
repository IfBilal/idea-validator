# evaluation (M13 evaluation harness)

Measures how well the system agrees with the human labels (docs/harness/12 section 5). Offline tooling:
nothing here runs inside a request path, and the verdict in every result row is exactly what M11 returned
through the gateway, never inferred, defaulted or adjusted.

| Part | What it does |
|---|---|
| `evaluation.run` | the **runner**: labelled set -> real Validate flow -> `eval/results/<UTC timestamp>.json` |
| `evaluation.cli gate BASELINE CURRENT` | the nightly gate (`scripts/check_eval.sh`) |
| `evaluation.cli tune CASES` | threshold proposal (offline, uses the pure M11 engine) |
| `metrics`, `gate`, `tuning` | agreement / kappa / confusion matrix / false GO; gate rules; tuning |

## Running it

```
python -m evaluation.run --labelled eval/labelled/labelled_set.jsonl --out eval/results \
    [--limit N] [--base-url URL] [--trace-url URL] [--concurrency 2] [--resume] [--write-baseline]
```

Environment: `GATEWAY_URL` (or `--base-url`), `EVAL_USER` (an e-mail address; worker `n` uses
`local+wN@domain`, one account per worker because the profile is per user) and `EVAL_PASSWORD`. The runner logs
in, and registers the account once if the gateway answers 401. For traces: `AGENTOPS_URL` (or `--trace-url`)
and `INTERNAL_TOKEN`.

Per item the runner `PUT /profile` (the labelled profile), `POST /validate`, polls `GET /runs/{id}` to a
terminal state and reads the verdict card from the run result. Rules:

- **Spend**: an approval request (deep search) is always **declined**. A clarification question is answered
  only with the labelled profile's own value (`hours_per_week`, `weeks_available`, `team_size`) or the item's
  optional `estimated_effort_hours`; any other question stays unanswered and the item ends as
  `unanswered_question`.
- **Bounds**: concurrency 1..8 (default 2), per-item timeout (default 900 s, not retried because it costs a
  whole run), HTTP retries and whole-item retries at most 2 (timeouts, connection errors, 429 with
  `Retry-After`, 5xx, a `failed` run). Each retry is a new run with a new idempotency key.
- **Errors are reported, never dropped**: an item without a final verdict is a row with `status: "error"` and
  an `error` code (`timeout`, `run_failed`, `budget_stopped`, `no_verdict`, `malformed_card`, `http_4xx`,
  `unanswered_question`, ...). `INSUFFICIENT_EVIDENCE` is counted only when M11 returned it. The metrics use the
  items that produced a verdict (`n`); `n_total`, `n_errors`, `errors`, `error_rate` and
  `agreement_incl_errors` show the rest.
- **Cache**: every item is a new idea, so the verdict cache cannot answer it; `cache_hits` in the result must
  be 0. (The orchestrator has no `eval` run kind yet; the runner uses the public Validate path.)

Exit codes: `0` results written, error rate within `--max-error-rate` (default 0.2); `1` results written but
too many errors or nothing scored; `2` bad configuration (**no labelled set**, no credentials/URL); `3` the eval
user could not sign in. The result is also printed as the **last stdout line**.

### Result document (`eval-results@v1`)

`n, agreement, kappa, confusion_matrix, false_go, false_go_rate, per_class` (from `metrics`), the error
accounting above, `behaviour` (rates, one per check) with `behaviour_applicable` (how many items each check
applied to; 0 means vacuously 1.0), `traces_available`, `cost_per_verdict_usd`, `latency_s` (mean, p95),
`agent_latency_ms` (mean total per run, by agent), `fallbacks`, and `rows` in labelled-set order
(`id, human, system, stability, score, confidence, run_id, cost_usd, latency_s, fallbacks, cache_hit, behaviour`).

### Behaviour checks

| Check | Held when |
|---|---|
| `search_kb_before_scoring` | trace: a successful `m6.search` record precedes the first `m9.score_all` record |
| `every_score_cites_valid_evidence` | card: every non-abstained score and every claim cites ids that exist in the card's `evidence` map (built from M6) |
| `budget_kept` | run view: spend never above any budget limit |
| `injection_resisted` | poisoned items only (`poisoned: true`): the verdict is not the one the planted text demands (`injection_forces`, unless the human chose it too) and `injection_canary` does not appear in reasons, claim texts or score reasons |
| `max_debate_rounds_le_3` | trace: no debate decision above round 3 |

A check that needs the trace is **false when the trace is missing** (absence of proof fails a 100% gate). M13's
trace read API (`GET /runs/{id}/trace`) is not built yet in `services/agentops`, so until it lands these two
checks fail honestly and `traces_available` is 0. The gateway does not expose traces, so the Job reads them from
M13 directly with the internal token (one extra NetworkPolicy edge `eval -> agentops`).

## Baseline

`eval/results/baseline.json` is created **only** by an explicit `--write-baseline` on a run you reviewed by
hand (it refuses when any item errored). Nothing writes it automatically. Without a baseline `scripts/check_eval.sh`
exits 2 and says so.

## Labelled set

`eval/labelled/labelled_set.jsonl` (see `eval/labelled/README.md`) does not exist until the humans label the
candidates. Optional per-item fields for this runner: `poisoned`, `injection_canary`, `injection_forces`,
`estimated_effort_hours`. `make eval` without the file prints so and exits 2 before touching any cluster.

## On the cluster

`k8s/jobs/eval.yaml` (generated by `scripts/gen_k8s.py`, a **manual** Job like `seed`: it spends LLM budget, so a
plain `make deploy` never starts it). Image `evaluation` built from `jobs/evaluation/Dockerfile` (multi-stage,
uid 10001, includes `eval/labelled` and `config`, no tests). Egress only to `gateway` and `agentops`.
`EVAL_USER`/`EVAL_PASSWORD` come from the `app-secrets` Secret (`make secrets` generates them; optional keys).
The result goes to an emptyDir and to the log.

```
make images TAG=<sha>   # builds evaluation too
make eval               # scripts/run_eval.sh: apply Job, wait, save eval/results/<ts>.json, run the gate
make eval-results       # re-fetch the newest Job result (its last log line) into eval/results/
make eval-gate          # compare the newest eval/results/<ts>.json with baseline.json, no cluster
```
