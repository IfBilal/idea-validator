# PROGRESS.md — Live Project State

> Agents: read this second (after CLAUDE.md) at the start of every session and update it at the end of every task.
> Humans: this is the single place to see where the project is.

## Status
| Field | Value |
|---|---|
| Current phase | **Phase 0-2 foundation merged (PR #12); in progress: M2 storage (this PR) + gateway, then Phase 5** |
| Current task | Dev B: M2 storage + cache in review (P2-T07, P2-T08), next gateway (P2-T09). Dev A: P0-T06..T08, P1-T03..T10 (P1-T04 k8s_up merged in #13, not verified end to end) |
| Gateway (P5-T07/T08, PR #41) | Run, idea, compare, challenge, what-if and SSE endpoints in review. Review rounds done: ownership 404s, idempotent run starts, stream caps, scrubbed M3 output, fail-closed stream open (503), run-scoped approval/question ids, closed verdict labels with card/row reconciliation. Open: M3 must accept `run_id` from the gateway and serve `GET /runs/{id}/events?after=` and `POST /runs/{id}/cancel`; M3 only runs `validate` so far. |
| Current phase | **Phases 2-5 mostly built: gateway, M2, M5 (+LLM proxy), M8, M9/M10, M12 and the k8s base are merged; M3 orchestrator (#27), M7 analyst (#35) and gateway runs (#41) are in review** |
| Current task | Dev B: finish reviews of #27/#35/#41, then Discover graph + challenge/what-if, M13 eval harness, behaviour tests, verdict tuning. Dev A: M6 KB (#29), scouts + ingestion (#30), UI (#33), Jenkins (#32), docker-compose (#31); atomic budget ledger in common/budget.py |
| Last green main commit | — |
| Last Jenkins deploy | — |
| Cards in KB | 0 / 1,500 (sources 0 / 3) |
| Retrieval baseline (Recall@5 / MRR) | — |
| Fine-tune examples (total / hand-checked) | 0 / 0 |
| Labelled verdict set | 0 |

## Team
| Role | Name | Roll no. | GitHub | Owns |
|---|---|---|---|---|
| Dev A (Platform, Data, Ops & UI) | Taimoor | 24i-3015 | T361 | M14, M5, M4, M6i, M6, M1 UI, M10t, M10 (serving), M13 (ops) |
| Dev B (Decision, Agents & Analysis) | Bilal | 24i-3166 | IfBilal | M11, M2, M1 gateway, M3, M9, M8, M7, M12, M13 (eval) |

## Current task plan
Dev B order after the merged foundation: finish in-review PRs (M2 storage #15, M5 hardening #22, LLM proxy #23) -> M3 Validate graph + budget ledger (subagent, resumes after the limit reset) -> M8 agents (subagent) -> M7 analyst (needs M6) -> gateway run/ideas/SSE endpoints -> Discover graph + challenge/what-if -> M13 eval harness -> behaviour tests -> verdict tuning (needs the labelled set, see eval/labelled/) -> Phase 11/12. Blocked on Dev A: M6 KB, scouts/ingestion (data), Kustomize/Jenkins (deploy).

## Phase checklist
| Phase | Status | Testing gate | Security gate |
|---|---|---|---|
| 0 Bootstrap & contracts | ☐ | ☐ | ☐ |
| 1 Walking skeleton on K8s | ☐ | ☐ | ☐ |
| 2 Foundations (M11, M2, M5) | ☐ | ☐ | ☐ |
| 3 Data (M4, M6i, M6) | ☐ | ☐ | ☐ |
| 4 Niches & features (M7) | ☐ | ☐ | ☐ |
| 5 Validate workflow | ☐ | ☐ | ☐ |
| 6 Discover workflow | ☐ | ☐ | ☐ |
| 7 UI complete | ☐ | ☐ | ☐ |
| 8 Fine-tuning & M10 | ☐ | ☐ | ☐ |
| 9 AgenticOps | ☐ | ☐ | ☐ |
| 10 Verdict tuning | ☐ | ☐ | ☐ |
| 11 Hardening & evidence | ☐ | ☐ | ☐ |
| 12 Report & submission | ☐ | ☐ | ☐ |

## Gate log
| Phase | Testing | Security | Date | Commit | Notes / evidence |
|---|---|---|---|---|---|

## Completed tasks
| Task | PR | Date | Notes |
|---|---|---|---|
| P0-T03 uv workspace, tooling, pre-commit | #12 | 2026-11-26 | Dev A's task, done early by Dev B to unblock |
| P0-T04 Pydantic schemas + drift test | #12 | 2026-11-26 | 12 models mirror `schemas/*.schema.json`; worked-example verdict is raw engine output (see 27) |
| P0-T05 libs/common (config, logging, http, tracing, errors) | #12 | 2026-11-26 | |
| P0-T09, P0-T10 README, AI log | #12 | 2026-11-26 | |
| P1-T01 service template + app factory | #12 | 2026-11-26 | Podman and Docker 29 builds verified |
| P1-T02 13 stub services | #12 | 2026-11-26 | all 13 images build |
| P2-T09 (rest) gateway: register/login/JWT, /profile, rate limits, CORS, security headers, 32 KiB body limit | in review | 2026-11-27 | 117 tests; limiter shared through Redis |
| P0-T07 CI workflow (Dev A) + trivy pin | #16 | 2026-11-27 | merged; Trivy found real CVEs, base image bumped in #21 |
| P2-T09 gateway: auth, JWT, /profile, Redis rate limits, headers, body limit | #17 | 2026-11-27 | security-reviewed; trusted-proxy hops, fail-closed limiter |
| P2-T10, T11 M5 fetch pipeline: SSRF guard, robots, rate limits, cache, sanitiser | #18 | 2026-11-27 | merged before its security review finished; fixes in #22 |
| P5-T01..T03 M9 scorers + M10 hosted wrapper | #20 | 2026-11-27 | built by a subagent, 662 tests; needs M5 /llm (#23) and M7 features to run end to end |
| P5-T04 M12 critic + rails + approval evaluation | #19 | 2026-11-27 | Taimoor's review found a real hard-rule-2 gap (names must be in evidence), fixed |
| M5 security hardening (ReDoS 12 s -> 0.0 s, compression, deadlines, cache, source-by-host) | #22 | 2026-11-27 | open |
| P2-T12 M5 LLM proxy | #23 | 2026-11-27 | open, ADR-008 |
| P2-T07 rest, P2-T08 M2 storage + Redis verdict cache | #15 | 2026-11-27 | open, approved except sync |
| P2-T01..T05 M11 verdict engine | #12 | 2026-11-26 | 24 golden vectors, 99% coverage, verdict-guardian reviewed twice |
| P2-T06 M2 tables + Alembic | #12 | 2026-11-26 | |
| P0-T07 pr-checks workflow repaired | #16 | 2026-11-27 | actionlint clean; ui-tests, retrieval-gate, manifests jobs added with their artifacts (P1-T03, P3-T12, P1-T05) |
| P1-T04 k8s_up.sh | #13 | 2026-11-27 | merged, NOT verified end to end (inotify limit, see Blocked) |
| P1-T04 k8s_up.sh verified end to end + guard against wrong kubectl context + `make k8s-verify` | this PR | 2026-11-27 | /usr/local/bin/kubectl is k3s here: scripts pin KUBECONFIG; Calico enforces NetworkPolicy |
| P3-T01 data sources doc + ADR-009 | this PR | 2026-11-27 | Reddit/Product Hunt optional; 3 keyless sources; limits marked V/R/H |
| /llm live against Groq | this PR | 2026-11-27 | both configured models were retired (404): config fixed, live availability test added |
| M5 hardening round 2 (adversarial findings on #22/#23) | this PR | 2026-11-27 | NFKC 30 s -> 1.6 s, redirect to disabled source, overall deadline, /llm worst-case estimate + TraceRecord |
| P1-T05 Kustomize base (generated), P1-T07 NetworkPolicies from the call graph | this PR | 2026-11-27 | kubeconform strict 57/57, kube-linter clean, server dry run clean; datastores ran under restricted PSA with data surviving restarts; edges enforced live. UI, CronJobs and Jobs still to add |
| P1-T06 secrets, P1-T08 deploy step, P1-T09 smoke test, migrate Job | this PR | 2026-11-27 | all 13 services + 4 datastores running on kind, 0 restarts; `make smoke` passes (register, login, profile round trip through the ingress). Jenkins and rollback (P1-T08 rest) still open |
| P2-T13 (rest) atomic budget ledger (`BudgetLedger.reserve`, Lua) used by the orchestrator | this PR | 2026-11-27 | real-Redis race test: 40 parallel reservations admit exactly the limit; same scenarios pass on the in-memory emulation |
| P0-T06/T08 dev compose stack + Makefile targets, P1-T07 netpol_test (issues #31, #32 part) | this PR | 2026-11-27 | `make up` healthy, `make migrate` creates the app schema, `make netpol-test` passes on the live stack with a negative control; seed/eval/replay say which task they wait for |
| P2-T07 (part), P2-T09 (part) M2 auth + profiles | #12 (merged) | 2026-11-26 | layering enforced by import-linter |
| P2-T07 (rest) ideas, versions, verdicts, runs, approvals, compare; P2-T08 Redis verdict cache | in review | 2026-11-27 | keys built by `common.cache_keys`; hit/miss counters on `/metrics` |

## Blocked / Needs human
- [x] GitHub repo + branch protection (done 2026-11-26; add required status checks once CI works)
- [x] Dev A: fix `.github/workflows/pr-checks.yml` (fails with a workflow-file error; `mypy services libs jobs` line must become `uv run mypy`) (P0-T07)
- [ ] LLM provider API key → local `.env` (never commit)
- [ ] GitHub token; Reddit app credentials; Product Hunt developer token (request on Day 1)
- [x] (done 2026-11-27, verified: 3-node kind + Calico + ingress + metrics-server; `make k8s-verify` proves NetworkPolicy enforcement; persist the sysctl in /etc/sysctl.d/99-kind.conf to survive a reboot) **kind needs `sudo sysctl -w fs.inotify.max_user_instances=512`** on Taimoor's laptop (currently 128; kubelet/nodes fail to boot while minikube + civicpulse also run). Persist in /etc/sysctl.d/99-kind.conf, then re-run `make k8s-up` (P1-T04 not yet verified end to end)
- [ ] Decide which laptop hosts kind + Jenkins (≥ 16 GB RAM)
- [ ] Colab / Kaggle account for fine-tuning
- [ ] Ask instructor the open questions in `docs/harness/27_ASSUMPTIONS_AND_OPEN_QUESTIONS.md` §B

## Decisions made during build (link ADRs)
- ADR-008 (M5 LLM proxy is a thin OpenAI-compatible adapter; LiteLLM stays pluggable).
- Three recurring jobs run the Dev B routine (incoming PRs, outgoing PRs, development + subagents). Subagents: max 3, Sonnet, worktrees; usage limit resets 17:10 PKT and the development job resumes them.
- ADR-001..007 (initial) in `docs/decisions/`
- Ownership rebalance (before Phase 0): UI moved Dev B → Dev A; M7, M12, M8 Skeptic/Change Suggester moved Dev A → Dev B (see 18 §1, 27 §A).

## Tracked follow-ups (not blocking a merge; do not lose them)
**M3 orchestrator (from the #27 review)**
1. ~~Budget check and charge are not atomic~~ DONE 2026-11-27: `BudgetLedger.reserve` is one Lua check-and-charge; StepRunner uses it; verified against real Redis (old sequence admitted 40 of 40 for a limit of 10, new admits exactly 10).
2. No restart recovery: `RunService._runs` is in memory; nothing re-attaches to the Postgres checkpointer after a pod restart (docs/harness/07 §1). Rehydrate from the M2 run mirror. Without `DATABASE_URL` the checkpointer is `MemorySaver`; in a cluster it must fail (`ENVIRONMENT=production` already does).
3. Spec deviations are listed openly in `services/orchestrator/README.md` (no M12 `/rail/input`, no M8 `/skeptic` so the -0.5 challenge penalty cannot apply yet, stability only traced, approval only for deep search, clarify after scorers, `scorer_concurrency` unused).
4. Ownership: M3 never compares `user_id` (GET/answer/approve are by run id and predictable question/approval ids). Acceptable only because M1 is the sole caller; the gateway run endpoints (P5-T07) must enforce it and have a cross-user test.
5. Trace inputs are an `input_hash` only (docs/harness/13 §8). The cache key has no `user_id` (safe only while idea ids are globally unique, which M2 guarantees). Register `RunStatus` with LangGraph's msgpack allow-list before strict mode becomes the default.
6. Not verified: real Redis/Postgres under true concurrency, and that M5/M4 write tokens, pages and cost into the same `budget:{run_id}` hash.

## Lessons / notes for future sessions
- Docker is not installed system-wide. A rootless Docker 29 lives in `~/.local/docker-rootless` (start: `DOCKERD_ROOTLESS_ROOTLESSKIT_NET=pasta ~/.local/docker-rootless/bin/dockerd-rootless.sh`, then `DOCKER_HOST=unix:///run/user/1000/docker.sock`). Its published ports do not reach the host here; use Podman (`podman run -p`) for local databases and set `TEST_DATABASE_URL` / `TEST_REDIS_URL`. CI uses real Docker + testcontainers.
- A bare `models/` line in `.gitignore` hid every Python `models/` package from git (found in review of #12). Ignore rules for ML weights must be anchored (`/models/`); `libs/common/tests/test_repo_hygiene.py` now fails if source files are ignored. Always verify a PR from a fresh clone (`git clone`, `uv sync --all-packages`, `make lint typecheck test`), not from your working tree.
- Stacked PRs: merge with "Create a merge commit", never squash (squash makes the PRs above it conflict).
- Pytest loads shared fixtures with `-p common.testing`; two `conftest.py` files with the same name confuse mypy, so name helpers uniquely.
- `ruff format` rewrites files after the pre-commit hook runs; if a commit fails on end-of-file fixes, re-stage and commit again.
