# 20 — Task Backlog (GitHub-issue-ready)

Format: `| ID | Task | Module | Owner | Depends on | Acceptance |`. Owner A = Platform/Data/Ops, B = Decision/Agents/UI.
Create issues with `python scripts/create_issues.py` (parses these tables, uses `gh issue create`, labels by phase + module).
One issue = one branch = one PR. Status is tracked in GitHub + `PROGRESS.md`.

## Phase 0: Bootstrap & contracts
| ID | Task | Module | Owner | Depends on | Acceptance |
|---|---|---|---|---|---|
| P0-T01 | Create GitHub repo, add both members, CODEOWNERS, protect main (PR only, 1 review, required checks, no direct push) | M14 | A | — | Screenshot of rules; direct push rejected |
| P0-T02 | Copy this harness into repo root; initialise PROGRESS.md; create issues from backlog | M14 | A | P0-T01 | Issues exist with labels |
| P0-T03 | uv workspace, root pyproject, ruff/mypy config, pre-commit (ruff, gitleaks, end-of-file), `.gitignore`, `.env.example` | M14 | A | P0-T01 | `make setup lint typecheck` green |
| P0-T04 | `libs/common/schemas`: Pydantic models for every schema in `schemas/` + drift test | all | B | P0-T03 | test fails if JSON Schema ≠ Pydantic schema |
| P0-T05 | `libs/common`: config loader, structlog JSON logging w/ redaction, ServiceClient (timeouts, retries), tracing span → stdout | all | B | P0-T03 | unit tests for retry policy + redaction |
| P0-T06 | docker-compose.yml (Postgres 16, Redis 7, Qdrant pinned) for dev only | M14 | A | P0-T03 | `make up` healthy |
| P0-T07 | `.github/workflows/pr-checks.yml` skeleton (lint-type, unit-tests, ui-tests placeholder, security) + PR template + issue templates + commitlint | M14 | A | P0-T03 | Checks run on first PR |
| P0-T08 | Makefile targets from CLAUDE.md §7 (stubs allowed where component not built yet) | M14 | A | P0-T03 | `make help` lists targets |
| P0-T09 | README skeleton (prereqs, quick start, architecture figure, links to harness) | docs | B | — | Renders on GitHub |
| P0-T10 | `docs/ai-usage-log.md`, `docs/contribution.md` initialised; first AI-log entry | docs | B | — | Files exist |

## Phase 1: Walking skeleton on Kubernetes
| ID | Task | Module | Owner | Depends on | Acceptance |
|---|---|---|---|---|---|
| P1-T01 | Service template (FastAPI app factory, /health /ready /metrics, request-id + trace middleware, Dockerfile multi-stage non-root, .dockerignore, tests) + `/new-service` command | all | B | P0-T05 | template service passes tests, image runs as UID 10001 |
| P1-T02 | Generate stub services: gateway, profile, orchestrator, scouts, tools, kb, analyst, ideation, validators, scorer, verdict, critic, agentops; each returns schema-valid stub data | all | B | P1-T01 | 13 images build |
| P1-T03 | UI shell: Vite React TS, Tailwind, shadcn, TanStack Query, runtime `config.js` entrypoint, nginx-unprivileged image, 1 component test | M1 | B | P0-T03 | `API_BASE_URL` changes without rebuild |
| P1-T04 | `scripts/k8s_up.sh`: local registry, kind config (no default CNI), Calico, ingress-nginx, metrics-server (all pinned) | M14 | A | — | `kubectl get nodes` Ready, calico pods Ready |
| P1-T05 | Kustomize base: namespace (PSA restricted), Deployments+Services for all modules, StatefulSets (postgres, redis, qdrant), PVCs, Ingress, HPA gateway, ConfigMaps generated from `config/` and `rubrics/`, CronJob + Job stubs | M14 | A | P1-T02, P1-T04 | `kustomize build` + kubeconform + kube-linter pass |
| P1-T06 | `scripts/create_secrets.sh` (from local .env; never committed) | M14 | A | P1-T05 | Secrets present in cluster |
| P1-T07 | NetworkPolicies (default deny, DNS, intra edges, ui→gateway only, tools egress) + `scripts/netpol_test.sh` | M5/M14 | A | P1-T05 | non-M5 egress fails, M5 egress OK (screenshot) |
| P1-T08 | Jenkins in Docker (+ docker CLI, kubectl), credentials (kubeconfig), SCM polling H/2, `Jenkinsfile` Build→Push→Deploy→Smoke, post-failure rollback | M14 | A | P1-T05 | green Jenkins run deploys all stubs |
| P1-T09 | `scripts/smoke_test.sh` (health of all through Ingress/port-forward + stub validate) + `push_images.sh` | M14 | A | P1-T08 | smoke passes in Jenkins |
| P1-T10 | GHA `docker` matrix job (buildx, Trivy CRITICAL fail, SBOM) + `manifests` job (kubeconform, kube-linter) | M14 | A | P1-T05 | required checks added to branch protection |
| P1-T11 | Stub Validate path: gateway → orchestrator → verdict (real engine skeleton) → card JSON | M1/M3 | B | P1-T02 | `curl /api/validate` returns schema-valid card |
| P1-T12 | Deliberate merge conflict exercise (both members) | M14 | A+B | P0-T01 | conflict PR resolved, screenshot saved |
| P1-T13 | Phase 1 screenshots: protected main + PR checks, pods running, NetworkPolicy test | docs | A | P1-T07..09 | files in docs/screenshots |
| P1-T14 | ADR-005 (cluster choice) finalised | docs | A | P1-T04 | ADR committed |

## Phase 2: Foundations
| ID | Task | Module | Owner | Depends on | Acceptance |
|---|---|---|---|---|---|
| P2-T01 | M11 models + config loader from ConfigMap (`/etc/idea-validator/verdict.yaml`) + validation | M11 | B | P1-T02 | invalid weights → 422 |
| P2-T02 | M11 gates + abstention + score + confidence + classification | M11 | B | P2-T01 | gate/threshold/confidence tests from 08 §7 pass |
| P2-T03 | M11 challenge adjustments + sensitivity (14 runs) | M11 | B | P2-T02 | worked example flips match vectors |
| P2-T04 | M11 what-if + value-of-information + `/config` | M11 | B | P2-T03 | what-if vectors pass |
| P2-T05 | M11 golden vectors test + hypothesis properties + determinism test; coverage ≥ 95% | M11 | B | P2-T04 | CI required job green |
| P2-T06 | M2 Alembic migrations for all app tables (04 §1) + migrate Job | M2 | B | P1-T05 | upgrade/downgrade test |
| P2-T07 | M2 repositories/services/routes (layered) for profiles (versioned), ideas, versions, verdicts, runs, approvals, conversations + import-linter layering contract | M2 | B | P2-T06 | unit + integration tests |
| P2-T08 | M2 verdict/score/retrieval cache helpers: key includes idea, profile, rubric, weights, engine versions; hit flag + metric | M2 | B | P2-T07 | test: changing any version → miss |
| P2-T09 | Gateway auth (register/login argon2id, JWT, lockout), `/profile` GET/PUT, rate limits, input validation, CORS + security headers | M1 | B | P2-T07 | auth tests incl. other-user 404 |
| P2-T10 | M5 allow-list + SSRF guard + robots + per-domain token bucket + cache | M5 | A | P1-T07 | unit tests + real HN fetch in cluster |
| P2-T11 | M5 sanitiser + injection corpus (≥ 25 cases) + `blocked_injections_total` | M5 | A | P2-T10 | poisoned fixture flagged |
| P2-T12 | M5 LiteLLM `/llm/v1/chat/completions` proxy, model aliases, cost per call, run budget enforcement (402) | M5 | A | P2-T10 | cost traced; over budget → 402 |
| P2-T13 | `libs/common` BudgetLedger (Redis) + M13 `/trace` minimal (append JSONL + index) | M13 | A | P1-T02 | trace from M5 visible via GET |

## Phase 3: Data
| ID | Task | Module | Owner | Depends on | Acceptance |
|---|---|---|---|---|---|
| P3-T01 | `docs/data-sources.md`: terms + robots review per source; keys requested | M4 | A | — | committed |
| P3-T02 | Scout framework (CLI, raw_pages, scout_state cursor, circuit breaker, metrics) | M4 | A | P2-T10 | unit tests incl. empty source |
| P3-T03 | HN scout (Firebase + Algolia Show/Ask HN) | M4 | A | P3-T02 | ≥ 1,000 raw items |
| P3-T04 | GitHub scout (search API by topics list in sources.yaml) | M4 | A | P3-T02 | ≥ 500 raw items |
| P3-T05 | Reddit and/or Product Hunt scout (official APIs) | M4 | A | P3-T02 | ≥ 300 raw items |
| P3-T06 | CronJobs per source + on-demand `/scout` endpoint | M4 | A | P3-T03 | CronJob runs visible |
| P3-T07 | Ingestion: mappers → IdeaCard, canonical URL, ids, exact + MinHash dedupe, card_sources, kind classification | M6i | A | P3-T03 | idempotency test |
| P3-T08 | Ingestion: chunking (card_id on every chunk), FastEmbed dense+sparse, Qdrant upsert, ingestion_runs, index_version bump | M6i | A | P3-T07 | ≥ 1,500 cards, ≥ 3 sources |
| P3-T09 | Snapshot/restore commands + seed Job + `make seed` | M6i | A | P3-T08 | fresh cluster seeded from snapshot |
| P3-T10 | M6 `/search` hybrid (RRF) + rerank + filters + quote extraction + cache; `/cards`, `/evidence`, `/evidence/batch`, `/stats` | M6 | A | P3-T08 | integration tests |
| P3-T11 | Label ≥ 20 retrieval queries (relevant card ids, both members) | eval | A+B | P3-T10 | queries.yaml complete |
| P3-T12 | Retrieval eval 4 modes + baseline.json + pytest gate + GHA required job; ADR-002 numbers | M6 | A | P3-T11 | gate fails on regression |

## Phase 4: Niches, trends, gaps, features
| ID | Task | Module | Owner | Depends on | Acceptance |
|---|---|---|---|---|---|
| P4-T01 | Clustering job (HDBSCAN + c-TF-IDF names, stable ids) → niches | M7 | A | P3-T08 | ≥ 15 named niches |
| P4-T02 | Monthly niche_stats + slope + status rising/flat/saturated | M7 | A | P4-T01 | status edge tests |
| P4-T03 | Gap detection → gaps table with evidence ids; `/gaps` | M7 | A | P4-T01 | non-empty gaps, ids valid |
| P4-T04 | `/niches`, `/niches/match`, `/features` (all of 09 §4) | M7 | A | P4-T02 | deterministic tests |

## Phase 5: Validate workflow
| ID | Task | Module | Owner | Depends on | Acceptance |
|---|---|---|---|---|---|
| P5-T01 | Rubric loader + feature priors + rubric files v1 with real examples per level | M9 | B | P4-T04 | each rubric has 6 anchors + ≥ 6 examples |
| P5-T02 | M10 wrapper in hosted mode (via M5) with schema validation, retry once, fallback logging, abstain | M10 | B | P2-T12 | behaviour test invalid JSON |
| P5-T03 | M9 four PydanticAI scorers (+ learning in feasibility scorer), parallel `/score-all`, evidence subset check, blend α, gate inputs (effort, blocker, duplicate_of) | M9 | B | P5-T01, P5-T02 | schema + abstain tests |
| P5-T04 | M12 `/check` (existence, competitor-in-KB, NLI, quote substring), `/objections/filter`, `/rail/input`, `/approval/evaluate` | M12 | A | P3-T10 | unit tests incl. unsupported claim |
| P5-T05 | M8 Skeptic + Change Suggester + Idea Rewriter agents | M8 | A | P3-T10 | schema tests |
| P5-T06 | M3 Validate LangGraph: nodes, routing (pure, tested), Postgres checkpointer, interrupts (question, approval), parallel scorers, retries/timeouts, budgets, memo, decision traces, Redis events | M3 | B | P5-T03, P2-T04 | behaviour tests pass |
| P5-T07 | Gateway run endpoints: `/validate`, `/runs/{id}`, SSE `/events` (Last-Event-ID), `/answer`, `/approve` | M1 | B | P5-T06 | SSE test |
| P5-T08 | Gateway `/ideas`, `/versions`, `/compare`, `/challenge`, `/whatif` + M3 sub-flows | M1/M3 | B | P5-T06 | U7/U8/U9 API tests |
| P5-T09 | Behaviour tests: invalid JSON, tool timeout, poisoned page, approval decline, cache hit | M13 | B | P5-T06 | all green in CI |

## Phase 6: Discover workflow
| ID | Task | Module | Owner | Depends on | Acceptance |
|---|---|---|---|---|---|
| P6-T01 | M8 Ideator + Gap Analyst agents (cite gap evidence, anti-copy check, profile fit) | M8 | B | P4-T03 | schema + citation validator tests |
| P6-T02 | M3 Discover graph: niches → ideate → gap → skeptic → critic loop ≤ 3, drop rules, Validate sub-graph per survivor (concurrency 3), ranking | M3 | B | P6-T01, P5-T06 | round cap + drop tests |
| P6-T03 | Gateway `/discover` + events `idea_dropped`, `partial_result` | M1 | B | P6-T02 | API test |

## Phase 7: UI
| ID | Task | Module | Owner | Depends on | Acceptance |
|---|---|---|---|---|---|
| P7-T01 | Auth pages + protected routes + token handling | M1 | B | P2-T09 | component tests |
| P7-T02 | Profile page (form, zod validation, version shown) | M1 | B | P7-T01 | U1 |
| P7-T03 | Live run panel (SSE hook, current agent, tool calls list, spend meter vs budget) | M1 | B | P5-T07 | U3 |
| P7-T04 | Clarify question card + Approval dialog (cost, budget, approve/decline) | M1 | B | P7-T03 | U4, U5 |
| P7-T05 | Verdict card (verdict + stability, gates, 6 scores + confidence + evidence links, weights, thresholds, confidence, sensitivity flips, versions, cached badge, IE "what was missing") | M1 | B | P5-T08 | U6, U10 |
| P7-T06 | What-if panel (candidates, apply → v2, side-by-side diff) + Challenge buttons (defended/removed) | M1 | A | P7-T05 | U7, U8 |
| P7-T07 | Discover page (niche input, ranked list, dropped ideas, open card) | M1 | B | P6-T03 | U2 |
| P7-T08 | History + versions + Compare (table + radar) | M1 | A | P5-T08 | U9 |
| P7-T09 | Trace viewer (tree from `/runs/{id}/trace`) | M1 | A | P9-T01 | screenshot |
| P7-T10 | Playwright E2E smoke (profile → validate → card → what-if) | M1 | A | P7-T06 | runs in Jenkins nightly |

## Phase 8: Fine-tuning & scoring model
| ID | Task | Module | Owner | Depends on | Acceptance |
|---|---|---|---|---|---|
| P8-T01 | Candidate builder (≥ 360, stratified dims × levels) | M10t | A | P3-T10, P5-T01 | candidates.jsonl |
| P8-T02 | Draft labels with hosted model (rubric prompt) | M10t | A | P8-T01 | drafts for all |
| P8-T03 | Review CLI + hand-check ≥ 60 (both members; `checked_by`) | M10t | A+B | P8-T02 | ≥ 60 marked |
| P8-T04 | Grouped split by idea + overlap assertion vs labelled set; JSONL files + data README | M10t | A | P8-T03, P10-T01(draft) | split test passes |
| P8-T05 | Colab/Kaggle notebook + `train.py` (Unsloth QLoRA, settings recorded, val loss plot) | M10t | A | P8-T04 | runs/<run>.json + loss PNG |
| P8-T06 | Export merged GGUF, release asset, sha256; model-import Job via M5 | M10t | A | P8-T05 | model in PVC |
| P8-T07 | M10 llama.cpp container + wrapper (constrained JSON, validate, retry, fallback, metric) ; M9 switched to M10-first | M10 | A | P8-T06, P5-T02 | /ready after warm-up |
| P8-T08 | Comparison base vs fine-tuned vs hosted (MAE, within-1, valid JSON, real ids, latency, cost) + failure case | M10 | A | P8-T07 | eval/results/finetune_comparison.json |

## Phase 9: AgenticOps
| ID | Task | Module | Owner | Depends on | Acceptance |
|---|---|---|---|---|---|
| P9-T01 | M13 trace store complete (JSONL PVC + traces_index), `/runs/{id}/trace`, `/runs` | M13 | A | P2-T13 | API tests |
| P9-T02 | Replay command (recorded mode) + test that replay reproduces verdict | M13 | A | P9-T01, P5-T06 | test green |
| P9-T03 | Registry endpoint + `scripts/check_registry.py` CI job | M13 | A | P5-T01 | CI fails on unversioned change |
| P9-T04 | Eval harness Job (agreement, kappa, confusion matrix, false-GO, behaviour checks) + `check_eval.sh` + Jenkins nightly stage | M13 | B | P10-T01 | results JSON |
| P9-T05 | Drift CronJob (freshness, coverage, verdict distribution shift) | M13 | A | P3-T08 | drift JSON |
| P9-T06 | Prometheus + Grafana (or /dashboard) with 6 required panels; dashboard JSON committed | M13 | A | P9-T01 | screenshot |
| P9-T07 | Behaviour test: empty source; extras (debate drop, budget stop, 3-round cap) | M13 | B | P6-T02 | green |

## Phase 10: Verdict tuning
| ID | Task | Module | Owner | Depends on | Acceptance |
|---|---|---|---|---|---|
| P10-T01 | Labelled set ≥ 40 (independent labels, resolution, labeller recorded) | eval | A+B | P5-T06 | labelled_set.jsonl |
| P10-T02 | Before run: confusion matrix + agreement + false-GO | eval | B | P9-T04 | results saved |
| P10-T03 | Tuning script (grid over T_go, T_no, floor, weights; k-fold to avoid overfit; false-GO weighted cost) | M11 | B | P10-T02 | proposal + plots |
| P10-T04 | Apply tuned values to ConfigMap (new weights_version) + ADR + after run | M11 | B | P10-T03 | after matrix saved |

## Phase 11: Hardening & evidence
| ID | Task | Module | Owner | Depends on | Acceptance |
|---|---|---|---|---|---|
| P11-T01 | Load script + `measure_resources.sh` → requests/limits set from data (CSV committed) | M14 | A | P8-T07 | table in report |
| P11-T02 | HPA demo under load | M14 | A | P11-T01 | screenshot |
| P11-T03 | Rollout + bad release + rollback demo | M14 | A | P1-T08 | screenshots |
| P11-T04 | Green + failing GHA run; green + failing Jenkins run screenshots | M14 | A+B | — | screenshots |
| P11-T05 | Security sweep: Trivy clean CRITICAL, gitleaks history, pip/npm audit, ZAP baseline (optional) | sec | B | — | report notes |
| P11-T06 | Fresh-clone start test on other laptop; README fixes | docs | B | — | ≤ 10 commands |
| P11-T07 | All UI screenshots (profile, Discover, verdict card, challenge, what-if v2, IE) + trace, dashboard, drift, retrieval, fine-tune comparison | docs | A+B | P7, P9 | checklist 100% |

## Phase 12: Report & submission
| ID | Task | Module | Owner | Depends on | Acceptance |
|---|---|---|---|---|---|
| P12-T01 | Report draft per outline (≤ 10 pages excl. screenshots) | docs | A+B | P11 | PDF |
| P12-T02 | ADRs ≥ 3 finalised (we have 7) | docs | A+B | — | docs/decisions |
| P12-T03 | AI-usage log complete | docs | A+B | — | table complete |
| P12-T04 | Contribution table vs `git shortlog -sne` + PR counts; signatures | docs | A+B | — | signed PDF/scan |
| P12-T05 | Markers can open repo (visibility/collaborators); GCR submission | — | A+B | all | submitted |
