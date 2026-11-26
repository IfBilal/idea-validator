# 17 — Implementation Plan (Phases 0–12)

Strategy (Guide §12): **get a thin version of everything running end-to-end on Kubernetes early, then improve each part.**
Never leave Docker, Jenkins or Kubernetes to the last days.

- Timeline: ~7 days, 2 developers. **Dev A = Platform, Data, Ops & UI**, **Dev B = Decision, Agents & Analysis** (see 18).
- Every phase ends with **TESTING GATE + SECURITY GATE** (`19_QUALITY_GATES.md`). The next phase does not start until both
  pass and results are recorded in `PROGRESS.md`. (Parallel tracks may continue on already-unblocked tasks.)
- Task IDs (`P3-T04`) are defined in `20_TASK_BACKLOG.md` with owner, dependencies and acceptance criteria.

| Phase | Name | Day | Owner(s) | Outcome |
|---|---|---|---|---|
| 0 | Bootstrap & contracts | D1 AM | A + B | Repo, protection, issues, uv workspace, schemas, CI skeleton |
| 1 | Walking skeleton on Kubernetes | D1 PM – D2 AM | A (+B) | All 14 services deployed as stubs on kind+Calico via Jenkins; NetworkPolicy test passes |
| 2 | Foundations: Verdict Engine, Profile/Auth/Cache, Tool Layer | D2 | B: M11, M2 · A: M5 | Engine complete with golden tests; auth+profile; M5 complete |
| 3 | Data: scouts, ingestion, KB search | D2 – D3 | A | ≥1,500 cards from ≥3 sources, snapshot, hybrid search, retrieval gate in CI |
| 4 | Niches, trends, gaps, features | D3 | B | M7 named niches, monthly series, gaps, `/features` |
| 5 | Validate workflow end-to-end | D3 – D4 | B | Validate runs: intake→clarify→evidence→features→4 scorers→verdict→challenge→stability→card |
| 6 | Discover workflow | D4 – D5 | B | Ideator/Gap Analyst/Skeptic debate ≤3 rounds, survivors validated, ranked results |
| 7 | UI complete (U1–U10) | D3 – D5 | A | All screens, live run, approval, what-if v2, challenge, compare, history |
| 8 | Fine-tuning & scoring model | D3 – D5 | A | ≥300 examples, ≥60 hand-checked, QLoRA model served in M10, comparison table |
| 9 | AgenticOps complete | D5 | A (B: eval harness) | Traces+replay, budgets, registry, eval Job, drift, dashboard, behaviour tests |
| 10 | Verdict tuning & evaluation | D6 | A + B | Labelled set, before/after confusion matrices, tuned ConfigMap, ADR |
| 11 | Hardening, measurement, demo evidence | D6 – D7 | A + B | Requests/limits measured, HPA, rollout/rollback, all screenshots |
| 12 | Report & submission | D7 | A + B | Report ≤10 pages, README, ADRs, AI log, contribution table, GCR submission |

---

## Phase 0: Bootstrap & contracts (D1 AM)
**Goal:** repo and contracts exist so both developers can work in parallel without blocking each other.
Tasks: P0-T01…P0-T10 (repo + protection + CODEOWNERS, issues from backlog, uv workspace + `libs/common`, Pydantic schemas
matching `schemas/*.json` + drift test, config loader, tracing client stub, Makefile, pre-commit (ruff, gitleaks), docker
compose with Postgres/Redis/Qdrant, `pr-checks.yml` skeleton, README skeleton, PROGRESS.md initialised).
**Exit criteria:** `make setup lint typecheck test` green locally and in GHA; main protected; first PR merged by review;
schemas importable from `libs/common`.

## Phase 1: Walking skeleton on Kubernetes (D1 PM – D2 AM)
**Goal:** the full deployment shape exists *now*. Every module is a container with `/health`, `/ready`, `/metrics`,
returning stub responses that match the schemas.
Tasks: P1-T01…P1-T14: service template generator (`/new-service`), 13 stub services + UI shell (runtime config.js),
Dockerfiles (multi-stage, non-root, pinned, `.dockerignore`), kind+Calico+registry+ingress+metrics-server script,
Kustomize base for all objects (Deployments, StatefulSets, CronJobs stubs, Jobs, ConfigMaps from `config/`, Secrets
script, Ingress, HPA), NetworkPolicies + `netpol_test.sh`, Jenkins container + Jenkinsfile (build/tag/push/deploy/smoke),
smoke test script, docker job + Trivy + kubeconform in GHA, **deliberate merge conflict exercise**.
**Exit criteria:** Jenkins green run deploys all stubs; `kubectl get pods` all Running/Ready; NetworkPolicy test shows
non-M5 blocked / M5 allowed; stub Validate call through Ingress returns a schema-valid fake verdict card via M3→M11 stub;
screenshots: protected main + PR with checks, merge conflict PR, all pods running, NetworkPolicy test.

## Phase 2: Foundations (D2)
**Dev B: M11 Verdict Engine (complete)**: port `eval/verdict/reference_engine.py` into `services/verdict` with typed
models, ConfigMap config, `/verdict`, `/whatif`, `/value-of-information`, `/config`; **every test from 08 §7 + all golden vectors +
hypothesis properties**. Then **M2**: Alembic migrations for all tables (04), repositories, services, routes (layered),
auth (register/login/JWT/lockout), profile versioning, idea/version/verdict persistence, verdict cache with versioned
keys + visible hits. Gateway `/auth/*`, `/profile`, rate limits, input validation.
**Dev A: M5 Tool Layer (complete)**: allow-list, robots, per-domain rate limit, cache, sanitiser (+ corpus tests), SSRF
guard, budget checks, LiteLLM `/llm` proxy with cost; NetworkPolicy allows only M5 egress (already) → verify real fetch.
**Exit criteria:** M11 coverage ≥ 95% & all vectors pass; user can register/login/save profile through Ingress; M5 fetches
HN item through allow-list with cache hit on second call; sanitiser blocks the poisoned fixture.

## Phase 3: Data (D2 – D3) (Dev A)
Scouts (HN, GitHub, Reddit and/or Product Hunt [+YC if allowed]) via M5, CronJobs + on-demand `/scout`, failure handling;
ingestion (normalise, dedupe exact+near, chunk with card_id, embed dense+sparse, upsert, ingestion_runs, index_version);
**≥1,500 cards from ≥3 sources**; snapshot + restore + seed Job; M6 `/search` hybrid + RRF + rerank + filters + evidence
quotes; `/stats` freshness/coverage; label 20+ retrieval queries; retrieval eval (4 modes) → `baseline.json`; CI gate.
**Exit criteria:** card counts per source shown by `/stats`; re-running ingestion adds 0 duplicates; Recall@5/MRR for 4 modes
recorded (ADR-002 numbers); retrieval gate job required in GHA.

## Phase 4: Niches, trends, gaps, features (D3) (Dev B)
M7 clustering → named niches; monthly `niche_stats`; rising/flat/saturated; gaps with evidence ids; `/niches/match`;
`/features` (all features in 09 §4) with unit tests.
**Exit criteria:** ≥ 15 named niches with status; gaps list non-empty with valid evidence ids; features deterministic.

## Phase 5: Validate workflow end-to-end (D3 – D4)
**Dev B:** M9 four scorers (PydanticAI, rubrics from `rubrics/`, feature priors, blend, abstain) using **hosted model via
M5** first (M10 wrapper in "hosted mode" so the contract is final); M3 LangGraph Validate graph with all states (07),
interrupts for questions/approval, parallel scorers, retries/timeouts, budgets, memoisation, decision traces; gateway
`/validate`, `/runs/{id}`, `/runs/{id}/events` (SSE), `/answer`, `/approve`, `/ideas*`, `/compare`, `/challenge`, `/whatif`.
M12 critic `/check` (existence + NLI + quote), `/rail/input`, `/objections/filter`, `/approval/evaluate`;
M8 Skeptic + Change Suggester (needed for Validate steps 6–7) are also Dev B, so M3 never waits on another developer for them.
**Exit criteria:** Validate on 3 sample ideas returns cards with all fields of U6; INSUFFICIENT_EVIDENCE case shows what's
missing (U10); approval pause/resume works (U4); clarifying question only asked when it changes the verdict (U5);
what-if creates v2 computed by M11 (U7); challenge defends/removes (U8); behaviour tests for invalid JSON, tool timeout,
poisoned page pass.

## Phase 6: Discover workflow (D4 – D5) (Dev B)
M8 Ideator + Gap Analyst (+ Skeptic from P5); M3 Discover graph with debate ≤ 3 rounds, dropping defeated ideas,
survivors through Validate sub-graph (concurrency 3), ranking; gateway `/discover`.
**Exit criteria:** Discover for a profile returns a ranked list (verdict + short reason + gap evidence) (U2); trace shows
rounds ≤ 3 and drop decisions; changing the profile changes results (U1).

## Phase 7: UI complete (D3 – D5) (Dev A; Dev B supplies the gateway API + SSE event contract)
React app: auth pages, profile form, Discover page, Validate chat-style page with live run panel (agent, tool calls,
spend), clarify question card, approval dialog, verdict card (verdict/stability badge, 4 gates, 6 scores w/ confidence +
evidence links, weights, thresholds, confidence, sensitivity flips, what-if list + apply, challenge buttons,
versions, cached badge), version diff, history, compare (table + radar), trace viewer link. Component tests for each (14 §2).
**Exit criteria:** every U1–U10 demonstrable in the browser through Ingress; component tests required in CI.

## Phase 8: Fine-tuning & scoring model (D3 – D5) (Dev A, mostly async while Colab trains)
Candidates (≥ 360), hosted draft labels, hand-check ≥ 60 (both members), grouped split, no overlap with labelled set;
QLoRA training on Colab/Kaggle with settings + loss recorded; merge + GGUF; release asset; model-import Job; M10 llama.cpp
container with constrained JSON, validation, retry, fallback, logging; comparison base vs fine-tuned vs hosted (6 metrics);
one failure case write-up; switch M9 to M10-first.
**Exit criteria:** M10 serving in cluster; comparison JSON + table; valid-JSON ≥ 95%; fallback metric visible.

## Phase 9: AgenticOps complete (D5) (Dev A; Dev B eval harness)
Trace storage (JSONL + index) + `/runs/{id}/trace` + UI trace view; replay command (recorded mode) + test; budgets in M3 + M5
verified; registry endpoint + CI registry check; eval harness Job (agreement, confusion matrix, false-GO, behaviour checks) +
Jenkins nightly stage + `check_eval.sh`; drift CronJob; Prometheus + Grafana dashboard with the 6 required panels; behaviour
tests: invalid JSON, tool timeout, empty source, poisoned page (+ extras).
**Exit criteria:** dashboard shows live data; a replayed run reproduces its verdict; eval Job writes results; drift report exists.

## Phase 10: Verdict tuning & evaluation (D6) (A + B)
Build labelled set (≥ 40 ideas: clear GOs, clear NO-GOs, borderline, thin-evidence; both label independently, resolve,
record who labelled what; never used for training); run eval → **before** confusion matrix; tune T_go, T_no, floor, weights
(grid search script with leave-out validation to avoid overfitting); report false-GO separately; final values in ConfigMap
(new `weights_version`), ADR with justification; run eval → **after** matrix; update baseline.
**Exit criteria:** before/after matrices + agreement + false-GO numbers in `eval/results/`; ConfigMap updated via PR.

## Phase 11: Hardening, measurement, demo evidence (D6 – D7) (A + B)
Measure requests/limits under load → set values; HPA demo; rollout + bad release + rollback demo; Trivy clean of
CRITICAL; OWASP ZAP baseline (optional); Playwright E2E; README "start in N commands" tested on the other member's laptop
from a clean clone; capture **every screenshot** in `docs/screenshots/CHECKLIST.md`.
**Exit criteria:** screenshot checklist 100%; fresh-clone start works; all gates green.

## Phase 12: Report & submission (D7) (A + B)
Report (≤ 10 pages + screenshots) following `docs/report/REPORT_OUTLINE.md`; ≥ 3 ADRs final; AI-usage log complete;
contribution table filled, signed, matching `git shortlog -sne` and PR authorship; repo access for markers verified;
submit on GCR: repo link, report PDF, screenshots, AI log + ADRs, contribution table.
**Exit criteria:** `23_DELIVERABLES_AND_REPORT.md` checklist fully ticked.

## Cut list if behind schedule (in this order, never cut mandatory items)
1. Playwright E2E → keep component tests only. 2. Grafana → `/dashboard` page reading `/metrics`. 3. Product Hunt/YC
source → keep 3 sources. 4. UMAP in clustering. 5. ZAP scan. 6. Radar chart in compare (table is enough).
**Never cut:** anything in `02_REQUIREMENTS.md` marked M.
