# Idea Generator with Go / No-Go: project report (DRAFT)

CS4032 Software Construction and Development, FAST-NUCES. Group of 2: Taimoor (24i-3015, `T361`) and Bilal (24i-3166, `IfBilal`). Repository: https://github.com/IfBilal/idea-validator

> Draft rules. Facts the repository proves are written out and cite a file. Anything that needs real data is `[MEASURE: ...]` naming the command or artifact that produces it. Before PDF export (at most 10 pages, screenshots excluded), grep for `[MEASURE` and `[TODO` and resolve all. Page budget follows `docs/report/REPORT_OUTLINE.md`. When this draft was written, `PROGRESS.md` said the UI, scouts, ingestion, model serving and the M13 stores were not built, so sections 4 to 6 hold the design plus placeholders.

## 1. Summary and changes from the architecture

We built a Discover / Validate system for students choosing a project. Discover proposes ideas from a profile and from gaps in the data. Validate takes an idea and returns `GO`, `PIVOT`, `NO-GO` or `INSUFFICIENT_EVIDENCE` with evidence, what would change it, and stability (`STABLE` / `BORDERLINE`). Each module is its own container in namespace `idea-validator`.

The rule everything is built around: **the language model never outputs or adjusts the verdict.** Models produce evidence-backed scores. The Verdict Engine (M11) is plain Python (no LLM, network, randomness or clock) and turns scores into the verdict (`docs/decisions/ADR-001-verdict-from-code.md`).

| Change from Figure 1 | Why | Where |
|---|---|---|
| Hosted-LLM calls go through M5 `/llm/v1/chat/completions` | "Only M5 reaches the internet" stays literally true; one place for keys, allow-list, cost | `docs/decisions/ADR-003-llm-calls-through-m5.md` |
| The M5 proxy is a thin OpenAI-compatible adapter, not the LiteLLM SDK (LiteLLM stays pluggable behind `Poster`) | Same SSRF guard and pinned address as `/fetch`; small image; exact offline-testable cost | `docs/decisions/ADR-008-llm-proxy-adapter.md` |
| Learning is scored by the Feasibility-for-this-student scorer (own rubric `learning@v1`) | Description names 4 scorers and 6 dimensions; both depend on the profile | `docs/decisions/ADR-004-learning-dimension-scorer.md` |
| Score = blend of computed-feature prior and model judgment (alpha 0.6, rounded to 0.5) | Description 5.1 asks for features plus judgment | `docs/decisions/ADR-006-score-blend.md`, `config/verdict.yaml` |
| M2 is a separate service from the M1 gateway | Layered backend as in Assignment 1 | `services/profile/`, `services/gateway/` |
| Third source is keyless; Reddit and Product Hunt optional | Approval/token cannot be scheduled; scraping or logging in is forbidden | `docs/decisions/ADR-009-third-keyless-data-source.md`, `docs/data-sources.md` |
| Fine-tuning runs locally on an 8 GB GPU with transformers + PEFT + bitsandbytes, not Unsloth on Colab | Same QLoRA method and hyperparameters; no data upload | `docs/decisions/ADR-010-finetune-on-local-8gb-gpu.md` |
| M7 clusters by deterministic agglomeration on TF-IDF, not HDBSCAN on embeddings (first slice) | No real cards yet; deterministic; to be revisited | `docs/decisions/ADR-011-analyst-clustering.md` |

Work split (`PROGRESS.md`): Dev A (Taimoor) owns M14, M5, M4, M6i, M6, the UI, M10t, M10 serving and M13 operations. Dev B (Bilal) owns M11, M2, the M1 gateway, M3, M9, M8, M7, M12 and M13 evaluation.

## 2. Modules and architecture

`[TODO: replace the Figure 1 copy in docs/harness with our own diagram: 13 deployed services, 4 datastores, NetworkPolicy edges from scripts/gen_k8s.py, telemetry edges to M13.]`

| Module | Directory | Responsibility (what exists) |
|---|---|---|
| M1 Gateway | `services/gateway` | Only API entrance: register/login (JWT), `/profile`, `/validate`, `/discover`, `/runs/{id}` (+ `/answer`, `/approve`, SSE `/events`), `/ideas`, versions, `/compare`, `/challenge`, `/whatif`; Redis rate limits, security headers, 32 KiB body limit |
| M1 UI | `services/ui` (not in repo yet) | React app; browser talks only to M1. `[TODO: describe once built]` |
| M2 Profile, Session, Cache | `services/profile` | Postgres + Alembic (users, profiles, ideas, versions, verdicts, runs, approvals); argon2id; Redis verdict cache with versioned keys (`libs/common/src/common/cache_keys.py`) |
| M3 Orchestrator | `services/orchestrator` | LangGraph state machines: Validate, Discover (debate capped at 3 rounds), challenge, what-if; parallel scorers, retries/timeouts, atomic Redis budget ledger, approvals and questions as interrupts, Postgres checkpointer |
| M4 Scouts | `services/scouts` | Skeleton only. `[TODO: update when built]` |
| M5 Tool Layer | `services/tools` | Only internet exit: `/fetch` (URL policy, robots.txt, per-domain rate limits, cache, sanitiser) and the `/llm` proxy with per-run cost accounting |
| M6 Knowledge Base | `services/kb` | Card store, chunking that keeps `card_id`, hybrid search (dense + BM25, RRF) with rerank, quotes, HTTP API |
| M6i Ingestion | `jobs/ingestion` (not in repo yet) | `[TODO: dedupe, chunk, embed, index, snapshot; issue #30]` |
| M7 Analyst | `services/analyst` | Deterministic niches, monthly series, rising/flat/saturated, gaps, features |
| M8 Idea Generator | `services/ideation` | Ideator, Gap Analyst, Skeptic, Change Suggester, Idea Rewriter; code-enforced rules (known gap, real evidence ids, effort vs hours, similarity to existing products) |
| M9 Validation | `services/validators` | Four parallel scorers, one rubric each (`rubrics/*@v1.yaml`), feature prior + model blend, abstain without valid evidence |
| M10 Scoring Model | `services/scorer` | Prompt builder, reply validation, hosted fallback through M5; llama.cpp serving is Dev A's task |
| M10t Fine-tuning | `jobs/finetune` | Candidates, draft labels, review CLI, split by idea, QLoRA train, export, compare, benchmark |
| M11 Verdict Engine | `services/verdict` | Gates, weights, thresholds, confidence, challenge, sensitivity, what-if; rules in `config/verdict.yaml` |
| M12 Critic | `services/critic` | Claim check, competitor-name check against the KB, input rail, approval evaluation, objection filtering |
| M13 AgenticOps | `services/agentops`, `jobs/evaluation` | Evaluation harness, metrics, tuning grid, regression gate in `jobs/evaluation`; trace store, registry and dashboard still skeleton in `services/agentops` |
| M14 Platform | `.github/workflows/pr-checks.yml`, `Jenkinsfile`, `k8s/`, `scripts/` | CI, delivery, Kubernetes |

Stack (`CLAUDE.md` section 6, `docs/harness/29_TECH_STACK.md`): Python 3.12 + uv, FastAPI + Pydantic v2, httpx, structlog JSON logs, PostgreSQL 16 + SQLAlchemy async + Alembic, Redis 7, Qdrant, LangGraph (ADR-007), PydanticAI, Qwen2.5-1.5B-Instruct + QLoRA, kind + Calico + ingress-nginx, Kustomize, GitHub Actions, Jenkins. Every service exposes `/health`, `/ready`, `/metrics`.

## 3. Verdict design

**Gates, in order** (`config/verdict.yaml`): Evidence (at least 3 sources on at least 2 platforms, else `INSUFFICIENT_EVIDENCE`); Feasibility (effort fits hours, else `NO-GO` with the hours gap); Risk (no legal/safety blocker, else `NO-GO` naming it); Duplicate (no same-user clone unless Differentiation >= 3, else `NO-GO` naming the product).

**Weights** (sum 100; description presets, unchanged so far): startup 25/20/20/15/15/5, portfolio 15/15/15/25/10/20, course project 10/10/15/35/10/20 over demand_pain, competition, differentiation, feasibility, risk, learning. `[MEASURE: if tuning changes a weight, list old/new and justification; source eval/results/tuning-*.json + ADR]`

**Thresholds**: `T_go 70`, `T_no 50`, floor `1.5` on the first five dimensions only. Score in [50,70) or a floor breach gives `PIVOT` naming the weakest dimension. **Confidence** = `0.7 x mean(dim confidence) + 0.3 x min(1, sources/5)`; below 0.5 gives `INSUFFICIENT_EVIDENCE`. **Challenge**: each accepted objection gives score -0.5 (max -1.0 per dimension) and confidence -0.1. **Sensitivity**: each weight +/-20% plus the least-confident score +/-1, 14 runs; any flip gives `BORDERLINE`. **What would make this GO**: the smallest change that changes the verdict (step 0.5, at most 3 candidates); applying it re-runs scorers and engine and stores a new version.

**Rubrics.** One versioned file per dimension, `rubrics/<dimension>@v1.yaml`. `[TODO: paste an 8-10 line excerpt of rubrics/competition@v1.yaml]`

**Why a model cannot decide.** (1) Scorer/critic/ideation output parsers discard unknown keys such as `verdict` and `decision`. (2) M11 is tested for purity and determinism (`services/verdict/tests/unit/test_purity_and_determinism.py`). (3) Rules live in a ConfigMap-mounted file with `engine_version`, `weights_version`, `thresholds_version`, `rubric_set`. (4) Every cache key includes `profile_version`, `rubric_version`, `weights_version`, `engine_version`, `idea_version` (`libs/common/src/common/cache_keys.py`, `libs/common/tests/test_cache_keys.py`).

**Evidence or abstain.** Scores carry `evidence_ids` that exist in M6; a scorer without valid evidence returns `score: null`, `confidence: 0` (`services/validators/tests/unit/test_validators_runner.py`). M12 rejects competitor names that are not KB cards. M8 drops ideas that cite unknown gaps or invented evidence ids (`services/ideation/tests/unit/test_ideation_rules.py`).

**Tests.** 24 golden vectors cover every gate, threshold edge, floor rule, challenge cap, sensitivity flip and what-if (`eval/verdict/test_vectors.json`, reference `eval/verdict/reference_engine.py`, production check `services/verdict/tests/unit/test_golden_vectors.py`). `[MEASURE: uv run pytest services/verdict --cov -q -> test count and coverage]`

**Worked example** (the description's portfolio example, reproduced as golden vectors): scores 3.5/2.0/3.0/4.0/3.5/4.5, weights 15/15/15/25/10/20, all gates pass. Weighted score 70.5, no floor breach, so `GO`. Feasibility weight -20% gives about 69.9, so the card reads `GO, BORDERLINE`. `[MEASURE: add one real idea run through the live system (POST /validate, then GET /ideas/{id}/versions/{n}); paste verdict, gates, six scores with evidence ids, confidence, stability; screenshot 14]`

**Tuning, before and after.** Tooling exists: `jobs/evaluation/src/evaluation/metrics.py` (agreement, kappa, confusion matrix, false-GO), `tuning.py` (bounded grid over thresholds and weights with k-fold; never edits `config/verdict.yaml`), `gate.py` (regression gate). The labelled set has candidates only (`eval/labelled/candidates.jsonl`), no human labels yet.

| | Before (`t1`, `w1`) | After |
|---|---|---|
| Hand-labelled items | `[MEASURE: eval/labelled]` | same |
| Agreement / kappa | `[MEASURE: eval/results/*.json]` | `[MEASURE]` |
| False-GO count | `[MEASURE]` | `[MEASURE]` |
| 4x4 confusion matrix | `[MEASURE]` | `[MEASURE]` |

## 4. Knowledge base

**Sources and ethics.** Official APIs only: Hacker News (Firebase, Algolia), GitHub REST, a third keyless source (ADR-009; Lobsters, Stack Exchange next). Reddit and Product Hunt are credential-gated and optional. Terms, robots behaviour and limits: `docs/data-sources.md`, `config/sources.yaml`. M5 enforces https only, exact-host allow-list, public addresses only, robots.txt (24 h cache), per-domain Redis token bucket, no cookies or logins, redirects re-checked per hop, size caps, a 30 s overall deadline (`services/tools/README.md`). No private data is kept.

**Counts.** `[MEASURE: cards per source and per month, total (target >= 1,500 from >= 3 sources), dedupe rate; restore snapshot with make seed (blocked on issue #30), query Postgres cards, save to eval/results/kb-counts-<date>.json]`. `[MEASURE: niches found and top rising/saturated niches from M7 POST /recompute]`

**Retrieval.** Qdrant dense + sparse BM25 with RRF, cross-encoder rerank of top 50, filters by date/source/niche, citations by `card_id` (`services/kb/src/svc_kb/services/search.py`, `docs/decisions/ADR-002-hybrid-search.md`). Evaluation: hand-written queries in `eval/retrieval/queries.yaml` (requirement: at least 20). `eval/retrieval/baseline.json` is still the `null` placeholder.

| Mode | Recall@5 | Recall@10 | MRR@10 | nDCG@10 |
|---|---|---|---|---|
| dense | `[MEASURE]` | `[MEASURE]` | `[MEASURE]` | `[MEASURE]` |
| BM25 | `[MEASURE]` | `[MEASURE]` | `[MEASURE]` | `[MEASURE]` |
| hybrid | `[MEASURE]` | `[MEASURE]` | `[MEASURE]` | `[MEASURE]` |
| hybrid + rerank | `[MEASURE]` | `[MEASURE]` | `[MEASURE]` | `[MEASURE]` |

`[MEASURE: make test-retrieval writes eval/results/retrieval-<date>.json once P3-T12 exists; screenshot 21]`

## 5. Fine-tuning

**Task.** One idea, one dimension, retrieved evidence in; `score`, `confidence`, `evidence_ids`, `reason` out. No verdict (`jobs/finetune/README.md`). **Settings**: base `Qwen/Qwen2.5-1.5B-Instruct`, QLoRA nf4, LoRA r=16 alpha 32 dropout 0.05 on q,k,v,o,gate,up,down, lr 2e-4 cosine, warmup 5%, early stopping on validation loss, seed 42, loss on assistant tokens only (`jobs/finetune/src/finetune/masking.py`), batch 2 x accumulation 8; deviations from the spec are listed in ADR-010. Export: merged, GGUF Q4_K_M, llama.cpp behind the M10 wrapper, hosted fallback through M5.

**Data.** Stratified candidates, drafted by a hosted model, hand-reviewed with `python -m finetune.review` by both members, split by idea with an overlap assertion against the labelled verdict set (`jobs/finetune/src/finetune/split.py`). `[MEASURE: examples per dimension x level, total (target >= 300), hand-checked (target >= 60), split sizes]`

**Measured so far (pipeline check on synthetic data, not a quality result).** Smoke figures are in `jobs/finetune/runs/smoke.json` and `jobs/finetune/runs/smoke_serving.json`. `[MEASURE: settings table and loss curve from the real run's jobs/finetune/runs/<run>.json and PNG]`

**Comparison** `[MEASURE: finetune compare output -> eval/results/finetune_comparison.json; screenshot 22]`:

| Model | MAE vs human | within-1 | valid JSON | real evidence ids | p50 latency | cost |
|---|---|---|---|---|---|---|
| base 1.5B | `[MEASURE]` | `[MEASURE]` | `[MEASURE]` | `[MEASURE]` | `[MEASURE]` | `[MEASURE]` |
| fine-tuned (Q4_K_M) | `[MEASURE]` | `[MEASURE]` | `[MEASURE]` | `[MEASURE]` | `[MEASURE]` | `[MEASURE]` |
| hosted | `[MEASURE]` | `[MEASURE]` | `[MEASURE]` | `[MEASURE]` | `[MEASURE]` | `[MEASURE]` |

**A case where fine-tuning did not help.** `[MEASURE: take it from the comparison output: an example where fine-tuned is not better than base or hosted; quote it and explain]`

## 6. AgenticOps results

Built and tested: JSON logs on every service; trace record schema (`schemas/trace_record.schema.json`) and client (`libs/common/src/common/tracing.py`), used by M3 and M5 `/llm`; per-run budgets in `config/budgets.yaml`, enforced in M3 by an atomic Redis check-and-charge (`libs/common/src/common/budget.py`, `libs/common/tests/test_budget_reserve.py`) and in M5 by page/byte limits; approvals above budget with a 600 s timeout counted as decline; versioned prompts (`prompts/`) and rubrics; evaluation harness (`jobs/evaluation`); injection defence (M5 sanitiser, `<evidence>` delimiters, critic). Not built yet (`PROGRESS.md`): trace store and `/runs/{id}/trace`, replay, registry endpoint, drift job, dashboard.

| Result | Value |
|---|---|
| Trace and replay reproducing a verdict | `[MEASURE: screenshot 18; python -m agentops.replay <run_id> once P9-T01/T02 exist]` |
| Budget stops and approvals | `[MEASURE: eval harness output, eval/results/*.json]` |
| Behaviour checks (empty source, debate drop, budget stop, 3-round cap) | `[MEASURE: eval harness output]` |
| Drift report | `[MEASURE: drift job output; screenshot 20]` |
| Dashboard (cost per verdict, latency per agent, cache hit rate, fallbacks, blocked injections) | `[MEASURE: screenshot 19]` |

## 7. Platform

**GitHub flow.** `main` protected (PR only, 1 review, required checks); conventional commits; PR template `.github/pull_request_template.md`; `.github/CODEOWNERS`; the other member reviews. `[MEASURE: screenshots 1-3]`

**GitHub Actions** (`.github/workflows/pr-checks.yml`, every PR): `lint-type` (ruff, format, import-linter, mypy), `unit-tests`, `integration-tests` (testcontainers), `contracts` (schema drift), `docker` (matrix of 13 images, buildx, Trivy CRITICAL), `manifests` (generator drift, kustomize, kubeconform strict, kube-linter), `security` (gitleaks, pip-audit, Trivy fs), `commitlint`. UI and retrieval-gate jobs arrive with their artifacts.

**Jenkins** (`Jenkinsfile`): SCM polling every 2 min, nightly timer, stages Build, Push, Deploy, Smoke, Eval (nightly), rollback on failure. Status: it references `scripts/push_images.sh`, `scripts/rollback_changed.sh` and `k8s/base/eval/eval-job.yaml`, which do not exist yet; no Jenkins build has run. `[MEASURE: screenshots 6, 7]`

**Docker.** One Dockerfile per service (`services/*/Dockerfile`): multi-stage, pinned `python:3.12.16-slim-bookworm`, non-root UID 10001, root `.dockerignore`, built from the repo root, tagged by commit SHA (`make images TAG=<sha>`), Trivy in CI.

**Kubernetes** (`k8s/`, ADR-005): kind + Calico, local registry `localhost:5001`, ingress-nginx, metrics-server, Kustomize `base/` + `overlays/dev`, namespace `idea-validator` with Pod Security `restricted`. Manifests are generated from one table in `scripts/gen_k8s.py` (test and CI job fail on drift): non-root, read-only root filesystem, no privilege escalation, seccomp RuntimeDefault, `/health` and `/ready` probes, ConfigMaps from `config/`, Secrets from `.env` (`scripts/create_secrets.sh`), a migrate Job (`k8s/base/jobs/migrate.yaml`), datastores (`k8s/base/data/`), gateway HPA (`k8s/base/hpa.yaml`), Ingress. NetworkPolicy: default deny, DNS, one rule per call-graph edge, internet egress only for `app: tools` (`k8s/base/netpol/`). `make netpol-test` (`scripts/netpol_test.sh`) shows a non-M5 pod failing and M5 succeeding. Rollback is `kubectl rollout undo`. `[MEASURE: screenshots 8-11]`

**Requests and limits.** Method: run `[MEASURE: scripts/measure_resources.sh, P11-T01, does not exist yet]` under a fixed load while sampling `kubectl top pod`; take p95 CPU and peak memory per service; set requests near p50 and limits at p95 plus headroom; commit the CSV. Current values come from `scripts/gen_k8s.py`, not measurement. `[MEASURE: table service | idle | loaded p95 | request | limit; HPA demo screenshot]`

**Security design.** Only M5 reaches the internet (NetworkPolicy). M5 connects to the checked address (no DNS rebinding). Fetched text is data: sanitised, wrapped in `<evidence>`, never obeyed, critic checks claims. Internal endpoints need `X-Internal-Token`; the browser talks to M1 only; JWT, argon2id with lockout, fail-closed Redis rate limits. Schemas validate inputs and outputs (`libs/common/tests/test_schema_drift.py`). No secrets in git (`.env.example` names only, gitleaks, `.pre-commit-config.yaml`). Checklist: `docs/harness/13_SECURITY.md`; first sweep recorded in `docs/`. `[MEASURE: gitleaks history, Trivy images, pip-audit/npm audit at the final gate]`

**AI usage.** AI wrote much of the code and harness docs. `docs/ai-usage-log.md` records what was asked, what humans changed and what a human must review (P12-T03 completes it). M11, M5 and auth code went through review agents and the other member's review. Decisions: `docs/decisions/` (ADR-001 to ADR-010).

## 8. Known limits and next steps

- No end-to-end run on real evidence until M4, M6i and the KB seed are done, so counts, retrieval metrics and real verdicts are `[MEASURE]`.
- The scoring model has no hand-checked training data yet (>= 60 reviews by both members needed).
- Thresholds and weights are starting values until tuned on a human-labelled set (about 40 items, so tuning is k-fold and conservative; a false GO is the costly error).
- The sanitiser is heuristic (letter-spacing obfuscation is not caught).
- M7 clustering quality is unmeasured on real data.
- Next: data path, tuning, UI, M13 stores, resource measurement, Jenkins, screenshots, signed contribution table.

## Appendix A. Requirement to evidence map

Status: built (code and tests), partial, not built.

### A.1 User stories

| ID | Requirement | Status | Where |
|---|---|---|---|
| U1 | Sign up, log in, save and edit profile | partial (API; UI not built) | `services/gateway/src/svc_gateway/api/routes.py`, `services/profile/tests/integration/test_profile_api.py`, `scripts/smoke_test.sh` |
| U2 | Discover: ranked ideas with verdict and reason | partial (graph built, no real data) | `services/orchestrator/tests/component/test_orch_discover.py`, `services/ideation/` |
| U3 | Watch agents live | partial (SSE; UI not built) | `services/gateway/tests/unit/test_gateway_streaming.py`, `services/gateway/tests/integration/test_redis_events.py` |
| U4 | Approve or decline deep search | built (API) | `services/critic/tests/unit/test_critic_approval.py`, `config/budgets.yaml` |
| U5 | Submit idea, answer only useful questions | built (API) | `services/orchestrator/tests/component/test_orch_validate_graph.py` |
| U6 | Verdict card | partial (data; UI not built) | `services/verdict/tests/component/test_verdict_api.py`, `schemas/verdict.schema.json` |
| U7 | What would make this GO, version 2 | built (API) | `services/verdict/tests/unit/test_whatif.py` |
| U8 | Challenge | built (API); Skeptic wiring partial | `services/orchestrator/tests/component/test_orch_challenge.py`, `services/verdict/tests/unit/test_challenge.py` |
| U9 | Compare ideas, history | built (API) | `services/profile/tests/integration/test_storage_api.py`, gateway `/compare`, `/ideas` |
| U10 | INSUFFICIENT EVIDENCE explained | built (engine) | `services/verdict/tests/unit/test_confidence_and_abstention.py`, `services/verdict/tests/unit/test_gates.py` |

### A.2 Modules

| Module | Status | Evidence |
|---|---|---|
| M1 | partial (gateway built, UI not) | `services/gateway/tests/component/`, `services/gateway/tests/integration/test_redis_ratelimit.py` |
| M2 | built | `services/profile/migrations/`, `libs/common/tests/test_cache_keys.py` |
| M3 | built (deviations in README) | `services/orchestrator/tests/`, `services/orchestrator/README.md` |
| M4 | not built | `services/scouts/`, `config/sources.yaml`, `docs/data-sources.md` |
| M5 | built (connectors come with M4) | `services/tools/tests/`, `k8s/base/netpol/tools-internet.yaml`, `scripts/netpol_test.sh` |
| M6 | partial (store and search built; seed and measurement missing) | `services/kb/tests/`, `eval/retrieval/queries.yaml` |
| M6i | not built | `[MEASURE: make seed after issue #30]` |
| M7 | built on fixtures | `services/analyst/tests/` |
| M8 | built | `services/ideation/tests/unit/test_ideation_rules.py`, `services/orchestrator/tests/unit/test_orch_debate.py` |
| M9 | built | `services/validators/tests/`, `rubrics/` |
| M10 | partial | `services/scorer/tests/`, `jobs/finetune/src/finetune/compare.py` |
| M10t | partial (pipeline only) | `jobs/finetune/`, `docs/decisions/ADR-010-finetune-on-local-8gb-gpu.md` |
| M11 | built | `services/verdict/tests/unit/`, `eval/verdict/test_vectors.json`, `config/verdict.yaml` |
| M12 | built | `services/critic/tests/unit/`, `services/tools/tests/unit/test_sanitiser.py` |
| M13 | partial | `jobs/evaluation/tests/`, `libs/common/tests/test_budget_reserve.py`, `libs/common/tests/test_tracing.py` |
| M14 | partial (Jenkins not running; rollback undemonstrated) | `.github/workflows/pr-checks.yml`, `Jenkinsfile`, `k8s/`, `libs/common/tests/test_k8s_manifests.py` |

### A.3 Submission list

| Item | Status | Where |
|---|---|---|
| Repository incl. README that starts the system | partial | `README.md`, `docs/FRESH_CLONE_TEST.md`, `scripts/fresh_clone_check.sh`; `[MEASURE: open the repo logged out]` |
| Report <= 10 pages | draft | this file |
| Screenshots with captions | not started | `docs/report/SCREENSHOTS_CHECKLIST.md` |
| AI-usage log | in progress | `docs/ai-usage-log.md` |
| >= 3 ADRs | built | `docs/decisions/` |
| Signed contribution table matching git | not started | `docs/contribution.md`; `[MEASURE: git shortlog -sne main, merged PRs per author, reviews per member]` |
