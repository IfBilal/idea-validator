# 26 — Requirements Traceability Matrix

Generated from `02_REQUIREMENTS.md` (regenerate with `python scripts/gen_rtm.py`). Columns: requirement → spec doc → phase → where it is tested → evidence for the report.
Agents: change **Status** (☐ → 🟡 in progress → ✅ done + PR #) as work completes. A requirement is ✅ only when tested AND evidenced.

| ID | Requirement | Source | Spec | Phase | Tested in | Evidence | Status |
|---|---|---|---|---|---|---|---|
| R-01 | Group of 2. Both members commit code. | D1.3 | CLAUDE.md, 15, 23 | P0/P12 | PR template, gitleaks, contribution check | AI log, ADRs, contribution table | ☐ |
| R-02 | Both review each other's pull requests. | D1.3, G4.2 | CLAUDE.md, 15, 23 | P0/P12 | PR template, gitleaks, contribution check | AI log, ADRs, contribution table | ☐ |
| R-03 | Both must be able to explain the whole system (keep `docs/harness/` + module READMEs readable). | D1.3 | CLAUDE.md, 15, 23 | P0/P12 | PR template, gitleaks, contribution check | AI log, ADRs, contribution table | ☐ |
| R-04 | AI tools and any library allowed; team responsible for every line. | D1.3 | CLAUDE.md, 15, 23 | P0/P12 | PR template, gitleaks, contribution check | AI log, ADRs, contribution table | ☐ |
| R-05 | Keep an AI-usage log: what AI wrote, what you changed, why (Date, Tool, Asked for, Changed, Why). | D1.3, G11.2 | CLAUDE.md, 15, 23 | P0/P12 | PR template, gitleaks, contribution check | AI log, ADRs, contribution table | ☐ |
| R-06 | Collect data only through official APIs or pages that allow it. | D1.3, G5 | CLAUDE.md, 15, 23 | P0/P12 | PR template, gitleaks, contribution check | AI log, ADRs, contribution table | ☐ |
| R-07 | Respect robots.txt and rate limits. | D1.3, G5 | CLAUDE.md, 15, 23 | P0/P12 | PR template, gitleaks, contribution check | AI log, ADRs, contribution table | ☐ |
| R-08 | Keep no private data. | D1.3, G5 | CLAUDE.md, 15, 23 | P0/P12 | PR template, gitleaks, contribution check | AI log, ADRs, contribution table | ☐ |
| R-09 | Never commit keys or passwords. | D1.3 | CLAUDE.md, 15, 23 | P0/P12 | PR template, gitleaks, contribution check | AI log, ADRs, contribution table | ☐ |
| R-10 | Required tech used: GitHub, GitHub Actions, Jenkins, Docker, Kubernetes, RAG, fine-tuning, AgenticOps. | D1.2 | CLAUDE.md, 15, 23 | P0/P12 | PR template, gitleaks, contribution check | AI log, ADRs, contribution table | ☐ |
| R-11 | Whole system runs on Kubernetes, one container per module. | D1.2, F1 | CLAUDE.md, 15, 23 | P0/P12 | PR template, gitleaks, contribution check | AI log, ADRs, contribution table | ☐ |
| FR-U1a | Sign up (email + password), log in. | D2 U1, G3 | 01 §3, 22 | P2,P7 | gateway API tests + UI component tests + E2E | screenshot: profile | ☐ |
| FR-U1b | Save, read, edit profile: skills, hours_per_week, weeks_available, team_size, compute (laptop/free_gpu/paid), goal (startup/portfolio/course_project), interests. | D2 U1, G2.1 | 01 §3, 22 | P2,P7 | gateway API tests + UI component tests + E2E | screenshot: profile | ☐ |
| FR-U1c | Profile changes later recommendations (profile version in cache keys; weights from goal; feasibility from hours). | D2 U1, D4 M2 | 01 §3, 22 | P2,P7 | gateway API tests + UI component tests + E2E | screenshot: profile | ☐ |
| FR-U2a | Discover in a niche or in general; returns ranked list; each idea has verdict + short reason. | D2 U2, D3.1 | 01 §3, 22 | P6,P7 | gateway API tests + UI component tests + E2E | screenshot: Discover results | ☐ |
| FR-U2b | Discover proposes **new ideas built from gaps** found in data. | D1.1, D3.1 | 01 §3, 22 | P6,P7 | gateway API tests + UI component tests + E2E | screenshot: Discover results | ☐ |
| FR-U3 | Live view: running agent, tool calls, spend, as they happen (SSE or WebSocket). | D2 U3, G3 | 01 §3, 22 | P5,P7 | gateway API tests + UI component tests + E2E | screenshot: live run/trace | ☐ |
| FR-U4 | Expensive deep search: state cost, wait for approve/decline before exceeding budget. | D2 U4, D4 M3/M12, G7 | 01 §3, 22 | P5,P7 | gateway API tests + UI component tests + E2E | screenshot: approval dialog | ☐ |
| FR-U5a | Validate own idea or a proposed one. | D1.1, D2 U5 | 01 §3, 22 | P5,P7 | gateway API tests + UI component tests + E2E | demo: clarifying question | ☐ |
| FR-U5b | Ask only clarifying questions that change the result. | D2 U5 | 01 §3, 22 | P5,P7 | gateway API tests + UI component tests + E2E | demo: clarifying question | ☐ |
| FR-U6 | Verdict card shows verdict, gates, six scores with evidence links, weights, confidence, stability. | D2 U6 | 01 §3, 22 | P5,P7 | gateway API tests + UI component tests + E2E | screenshot: verdict card | ☐ |
| FR-U7 | "What would make this GO?": concrete changes proposed; applying one gives version 2 computed by the engine. | D2 U7, D5.3 | 01 §3, 22 | P5,P7 | gateway API tests + UI component tests + E2E | screenshot: what-if v2 | ☐ |
| FR-U8 | "Challenge" a claim: critic re-checks against evidence; defends or removes. | D2 U8 | 01 §3, 22 | P5,P7 | gateway API tests + UI component tests + E2E | screenshot: challenge | ☐ |
| FR-U9 | History of ideas + versions; compare two ideas on all six dimensions. | D2 U9, G3 | 01 §3, 22 | P5,P7 | gateway API tests + UI component tests + E2E | screenshot: compare/history | ☐ |
| FR-U10 | INSUFFICIENT EVIDENCE card says what was missing and does not guess. | D2 U10, D5.2 | 01 §3, 22 | P5,P7 | gateway API tests + UI component tests + E2E | screenshot: INSUFFICIENT EVIDENCE | ☐ |
| M1-01 | The only entrance (Ingress → UI/API). Chat, live progress, verdict card, approval prompt, history. | D4, F1, G9 | 05, 22, 13 | P1,P2,P7 | services/gateway/tests, services/ui tests | screenshots, HPA | ☐ |
| M1-02 | API address set at runtime (not baked into the bundle). | D4 | 05, 22, 13 | P1,P2,P7 | services/gateway/tests, services/ui tests | screenshots, HPA | ☐ |
| M1-03 | Login (JWT). | D4 | 05, 22, 13 | P1,P2,P7 | services/gateway/tests, services/ui tests | screenshots, HPA | ☐ |
| M1-04 | Rate limits (per IP and per user). | D4 | 05, 22, 13 | P1,P2,P7 | services/gateway/tests, services/ui tests | screenshots, HPA | ☐ |
| M1-05 | Input validation on every endpoint (Pydantic, size limits). | D4, G2 | 05, 22, 13 | P1,P2,P7 | services/gateway/tests, services/ui tests | screenshots, HPA | ☐ |
| M1-06 | Component tests for the UI. | D4, G8.1 | 05, 22, 13 | P1,P2,P7 | services/gateway/tests, services/ui tests | screenshots, HPA | ☐ |
| M1-07 | Public endpoints of Guide §3 incl. `/metrics`, `/health`, `/ready`. | G3 | 05, 22, 13 | P1,P2,P7 | services/gateway/tests, services/ui tests | screenshots, HPA | ☐ |
| M1-08 | API gateway autoscaled (HPA). | G9.1, G-F3 | 05, 22, 13 | P1,P2,P7 | services/gateway/tests, services/ui tests | screenshots, HPA | ☐ |
| M1-09 | UI reaches API only (NetworkPolicy). | G-F3 | 05, 22, 13 | P1,P2,P7 | services/gateway/tests, services/ui tests | screenshots, HPA | ☐ |
| M2-01 | Postgres for users, profiles, ideas, versions, runs (tables of Guide Fig 1). | D4, G2 | 04, 05 | P2 | services/profile/tests (unit+integration) | report, code | ☐ |
| M2-02 | Redis for cached verdicts and retrieval, with TTL. Redis holds only cache entries. | D4, G-F1 | 04, 05 | P2 | services/profile/tests (unit+integration) | report, code | ☐ |
| M2-03 | Migrations (Alembic). | D4 | 04, 05 | P2 | services/profile/tests (unit+integration) | report, code | ☐ |
| M2-04 | Cache keys include profile and rubric versions (we also add idea, weights, engine versions). | D4, G12 | 04, 05 | P2 | services/profile/tests (unit+integration) | report, code | ☐ |
| M2-05 | Cache hits are visible (UI badge, trace `cache_hit`, metric). | D4 | 04, 05 | P2 | services/profile/tests (unit+integration) | report, code | ☐ |
| M2-06 | Layered backend: routes → services → repositories (as in Assignment 1). | D4 | 04, 05 | P2 | services/profile/tests (unit+integration) | report, code | ☐ |
| M3-01 | Supervisor that runs Discover and Validate as **testable state machines**. | D4, F1 | 07 | P5,P6 | services/orchestrator/tests (routing, behaviour) | trace screenshot | ☐ |
| M3-02 | Parallel agents (4 scorers concurrently). | D4, D3.2 | 07 | P5,P6 | services/orchestrator/tests (routing, behaviour) | trace screenshot | ☐ |
| M3-03 | Retries and timeouts on every call. | D4 | 07 | P5,P6 | services/orchestrator/tests (routing, behaviour) | trace screenshot | ☐ |
| M3-04 | Stop at budget and ask the user. | D4, G7 | 07 | P5,P6 | services/orchestrator/tests (routing, behaviour) | trace screenshot | ☐ |
| M3-05 | Re-run only what changed (step memoisation by input hash). | D4 | 07 | P5,P6 | services/orchestrator/tests (routing, behaviour) | trace screenshot | ☐ |
| M3-06 | Every decision traced. | D4, G7 | 07 | P5,P6 | services/orchestrator/tests (routing, behaviour) | trace screenshot | ☐ |
| M3-07 | Debate loop steps 3–6 repeats up to 3 rounds. | D3.1, D4 M8 | 07 | P5,P6 | services/orchestrator/tests (routing, behaviour) | trace screenshot | ☐ |
| M4-01 | Bring in data from ≥ 3 sources. | D4 | 10 §4 | P3 | services/scouts/tests | report: sources/counts | ☐ |
| M4-02 | Scheduled (CronJob) and on demand. | D4, G9 | 10 §4 | P3 | services/scouts/tests | report: sources/counts | ☐ |
| M4-03 | Per-source rate limits and failure handling. | D4 | 10 §4 | P3 | services/scouts/tests | report: sources/counts | ☐ |
| M4-04 | Internet only through M5. | D4, F1 | 10 §4 | P3 | services/scouts/tests | report: sources/counts | ☐ |
| M4-05 | Official APIs first (HN Firebase, GitHub REST, Reddit/PH official APIs with keys). | G5 | 10 §4 | P3 | services/scouts/tests | report: sources/counts | ☐ |
| M5-01 | Single controlled exit to the internet. | D4, F1 | 10 §3, 13 | P2 | services/tools/tests (+ netpol_test.sh) | NetworkPolicy test screenshot | ☐ |
| M5-02 | Connectors per source. | D4 | 10 §3, 13 | P2 | services/tools/tests (+ netpol_test.sh) | NetworkPolicy test screenshot | ☐ |
| M5-03 | Domain allow-list. | D4 | 10 §3, 13 | P2 | services/tools/tests (+ netpol_test.sh) | NetworkPolicy test screenshot | ☐ |
| M5-04 | Rate limits (per domain), clear User-Agent, back-off on errors. | D4, G5 | 10 §3, 13 | P2 | services/tools/tests (+ netpol_test.sh) | NetworkPolicy test screenshot | ☐ |
| M5-05 | robots.txt check. | D4, G5 | 10 §3, 13 | P2 | services/tools/tests (+ netpol_test.sh) | NetworkPolicy test screenshot | ☐ |
| M5-06 | Cache of fetches. | D4, G5 | 10 §3, 13 | P2 | services/tools/tests (+ netpol_test.sh) | NetworkPolicy test screenshot | ☐ |
| M5-07 | Sanitiser that strips instructions aimed at models. | D4, G5 | 10 §3, 13 | P2 | services/tools/tests (+ netpol_test.sh) | NetworkPolicy test screenshot | ☐ |
| M5-08 | NetworkPolicy so only M5 reaches the internet. | D4, G9.3 | 10 §3, 13 | P2 | services/tools/tests (+ netpol_test.sh) | NetworkPolicy test screenshot | ☐ |
| M5-09 | `POST /fetch` internal endpoint. | G3 | 10 §3, 13 | P2 | services/tools/tests (+ netpol_test.sh) | NetworkPolicy test screenshot | ☐ |
| M5-10 | Hosted-LLM proxy `POST /llm` (because only M5 may reach the internet). Budget-accounted. | derived from D4+G9.3 | 10 §3, 13 | P2 | services/tools/tests (+ netpol_test.sh) | NetworkPolicy test screenshot | ☐ |
| M5-11 | Page/token budgets enforced in tool layer too. | G7 | 10 §3, 13 | P2 | services/tools/tests (+ netpol_test.sh) | NetworkPolicy test screenshot | ☐ |
| M6-01 | Searchable memory of ideas; source of every piece of evidence. | D4 | 10 §6-7 | P3 | services/kb/tests, eval/retrieval | retrieval result screenshot | ☐ |
| M6-02 | Hybrid search (keyword + vector) with reranking. | D4, G11.1 | 10 §6-7 | P3 | services/kb/tests, eval/retrieval | retrieval result screenshot | ☐ |
| M6-03 | Filters by date, source and niche. | D4 | 10 §6-7 | P3 | services/kb/tests, eval/retrieval | retrieval result screenshot | ☐ |
| M6-04 | Card ids for citations (`card_id#c<n>` evidence ids). | D4, G2.2 | 10 §6-7 | P3 | services/kb/tests, eval/retrieval | retrieval result screenshot | ☐ |
| M6-05 | Freshness and coverage tracking. | D4, F1 | 10 §6-7 | P3 | services/kb/tests, eval/retrieval | retrieval result screenshot | ☐ |
| M6-06 | Retrieval quality measured on ≥ 20 hand-written queries; gate in CI. | D4, G8.1 | 10 §6-7 | P3 | services/kb/tests, eval/retrieval | retrieval result screenshot | ☐ |
| M6-07 | `POST /search` returns ranked evidence items with ids. | G3 | 10 §6-7 | P3 | services/kb/tests, eval/retrieval | retrieval result screenshot | ☐ |
| M6i-01 | Turn fetched pages into idea cards: dedupe, chunk, embed, index. | D4 | 10 §5 | P3 | jobs/ingestion/tests (idempotency) | card counts in report | ☐ |
| M6i-02 | Repeatable without duplicates (idempotent upserts). | D4 | 10 §5 | P3 | jobs/ingestion/tests (idempotency) | card counts in report | ☐ |
| M6i-03 | ≥ 1,500 cards from ≥ 3 sources. | D4 | 10 §5 | P3 | jobs/ingestion/tests (idempotency) | card counts in report | ☐ |
| M6i-04 | ingestion_runs recorded (run_id, source, added, dropped). | G-F1 | 10 §5 | P3 | jobs/ingestion/tests (idempotency) | card counts in report | ☐ |
| M6i-05 | Snapshot of card data so the system does not depend on websites being up. | G5 | 10 §5 | P3 | jobs/ingestion/tests (idempotency) | card counts in report | ☐ |
| M6i-06 | Every card keeps source and URL (credit). | G5 | 10 §5 | P3 | jobs/ingestion/tests (idempotency) | card counts in report | ☐ |
| M7-01 | Cluster cards into **named** niches. | D4 | 10 §8 | P4 | services/analyst/tests | niche map in report | ☐ |
| M7-02 | Rising, flat and saturated niches; monthly time series (niche_stats: launches, mentions). | D4, G-F1 | 10 §8 | P4 | services/analyst/tests | niche map in report | ☐ |
| M7-03 | A list of gaps that feeds M8 and M9. | D4 | 10 §8 | P4 | services/analyst/tests | niche map in report | ☐ |
| M7-04 | Computes features for scorers (Validate step 4 is M6 + M7). | D3.2 | 10 §8 | P4 | services/analyst/tests | niche map in report | ☐ |
| M8-01 | Ideator, Gap Analyst, Skeptic debate to propose ideas that fit the profile. | D4, D3.1 | 09 §2 | P5,P6 | services/ideation/tests | Discover trace | ☐ |
| M8-02 | Limited debate rounds (≤ 3). | D4, D3.1 | 09 §2 | P5,P6 | services/ideation/tests | Discover trace | ☐ |
| M8-03 | Every idea cites its gap evidence. | D4 | 09 §2 | P5,P6 | services/ideation/tests | Discover trace | ☐ |
| M8-04 | Ideas defeated with evidence are dropped. | D4, D3.1 | 09 §2 | P5,P6 | services/ideation/tests | Discover trace | ☐ |
| M8-05 | Skeptic argues against ideas with evidence (also in Validate step 6 Challenge). | D3.1, D3.2 | 09 §2 | P5,P6 | services/ideation/tests | Discover trace | ☐ |
| M8-06 | Proposes concrete "what would make GO" change texts targeting the engine's chosen dimension. | D2 U7 | 09 §2 | P5,P6 | services/ideation/tests | Discover trace | ☐ |
| M9-01 | Four scorers in parallel: Demand & Pain; Competition & Differentiation; Feasibility for this student; Risk. | D4, F1, D3.2 | 09 §5 | P5 | services/validators/tests | verdict card evidence links | ☐ |
| M9-02 | Own rubric, tools and output schema each. | D4 | 09 §5 | P5 | services/validators/tests | verdict card evidence links | ☐ |
| M9-03 | Computed features plus model judgment. | D4, D5.1 | 09 §5 | P5 | services/validators/tests | verdict card evidence links | ☐ |
| M9-04 | Every score cites evidence ids, otherwise the scorer abstains. | D4 | 09 §5 | P5 | services/validators/tests | verdict card evidence links | ☐ |
| M9-05 | Six dimension scores produced (Learning & portfolio value included; see ADR-004). | D5.1 | 09 §5 | P5 | services/validators/tests | verdict card evidence links | ☐ |
| M10-01 | Fine-tuned 1B–3B model (LoRA or QLoRA) in its own container, applies the rubrics. | D4 | 11 | P5,P8 | services/scorer/tests, finetune.compare | fine-tuning comparison screenshot | ☐ |
| M10-02 | ≥ 300 training examples covering all six dimensions and all score levels. | D4, G6.1 | 11 | P5,P8 | services/scorer/tests, finetune.compare | fine-tuning comparison screenshot | ☐ |
| M10-03 | ≥ 60 checked by hand, marked, used for evaluation. | D4, G6.1 | 11 | P5,P8 | services/scorer/tests, finetune.compare | fine-tuning comparison screenshot | ☐ |
| M10-04 | Split by idea (80/20); never use labelled-verdict-set ideas for training. | G6.1 | 11 | P5,P8 | services/scorer/tests, finetune.compare | fine-tuning comparison screenshot | ☐ |
| M10-05 | JSONL chat format with system/user/assistant. | G6.1 | 11 | P5,P8 | services/scorer/tests, finetune.compare | fine-tuning comparison screenshot | ☐ |
| M10-06 | Record settings (rank, lr, epochs, batch size) in repo; watch validation loss. | G6.2 | 11 | P5,P8 | services/scorer/tests, finetune.compare | fine-tuning comparison screenshot | ☐ |
| M10-07 | Merge adapter; optionally quantise (GGUF). | G6.2 | 11 | P5,P8 | services/scorer/tests, finetune.compare | fine-tuning comparison screenshot | ☐ |
| M10-08 | Serve behind `POST /score`, `/health`, `/ready`. | G6.2, G3 | 11 | P5,P8 | services/scorer/tests, finetune.compare | fine-tuning comparison screenshot | ☐ |
| M10-09 | Large weights not in git (release asset or registry). | G6.2 | 11 | P5,P8 | services/scorer/tests, finetune.compare | fine-tuning comparison screenshot | ☐ |
| M10-10 | Validate output vs schema; invalid → retry once → hosted fallback → log fallback. | D4, G6.2 | 11 | P5,P8 | services/scorer/tests, finetune.compare | fine-tuning comparison screenshot | ☐ |
| M10-11 | Comparison of base, fine-tuned, hosted: MAE, within-1, valid-JSON, real-evidence-id share, latency, cost. | D4, G6.3 | 11 | P5,P8 | services/scorer/tests, finetune.compare | fine-tuning comparison screenshot | ☐ |
| M10-12 | One example where fine-tuning did not help, explained. | G6.3, D6 | 11 | P5,P8 | services/scorer/tests, finetune.compare | fine-tuning comparison screenshot | ☐ |
| M10t-01 | Data, scripts and documented commands in the repository. | D4 | 11 | P8 | jobs/finetune/tests (split) | repo jobs/finetune | ☐ |
| M10-13 | K8s requests/limits from measurements. | G6.2 | 11 | P5,P8 | services/scorer/tests, finetune.compare | fine-tuning comparison screenshot | ☐ |
| M11-01 | Plain code, no LLM. | D4, D5 | 08 | P2,P10 | services/verdict/tests + golden vectors | report worked example + confusion matrices | ☐ |
| M11-02 | Same input gives same output (deterministic). | D4 | 08 | P2,P10 | services/verdict/tests + golden vectors | report worked example + confusion matrices | ☐ |
| M11-03 | Rules in a ConfigMap (weights, thresholds, floors). | D4, G9 | 08 | P2,P10 | services/verdict/tests + golden vectors | report worked example + confusion matrices | ☐ |
| M11-04 | Unit tests for every gate, threshold edge, sensitivity check and what-if. | D4 | 08 | P2,P10 | services/verdict/tests + golden vectors | report worked example + confusion matrices | ☐ |
| M11-05 | Gates first in order Evidence, Feasibility, Risk, Duplicate (with stated reasons). | D5.2, F4 | 08 | P2,P10 | services/verdict/tests + golden vectors | report worked example + confusion matrices | ☐ |
| M11-06 | Weighted score 0–100 from goal weights summing to 100. | D5.3 | 08 | P2,P10 | services/verdict/tests + golden vectors | report worked example + confusion matrices | ☐ |
| M11-07 | Confidence formula and < 0.5 → INSUFFICIENT EVIDENCE. | D5.3, F4 | 08 | P2,P10 | services/verdict/tests + golden vectors | report worked example + confusion matrices | ☐ |
| M11-08 | GO / PIVOT (names weakest dimension) / NO-GO rules with T_go, T_no, floor. | D5.3, F4 | 08 | P2,P10 | services/verdict/tests + golden vectors | report worked example + confusion matrices | ☐ |
| M11-09 | Challenge adjustments (−0.5/obj, max −1.0, conf −0.1). | D5.3 | 08 | P2,P10 | services/verdict/tests + golden vectors | report worked example + confusion matrices | ☐ |
| M11-10 | Sensitivity: weights ±20% shared proportionally + least-confident score ±1 → BORDERLINE + what flips it. | D5.3, F4 | 08 | P2,P10 | services/verdict/tests + golden vectors | report worked example + confusion matrices | ☐ |
| M11-11 | What-would-make-GO: smallest change that changes the verdict. | D5.3 | 08 | P2,P10 | services/verdict/tests + golden vectors | report worked example + confusion matrices | ☐ |
| M11-12 | `POST /verdict`, `POST /whatif`. | G3 | 08 | P2,P10 | services/verdict/tests + golden vectors | report worked example + confusion matrices | ☐ |
| M11-13 | Verdict JSON exactly as Guide §2.3 (+ our additions). | G2.3 | 08 | P2,P10 | services/verdict/tests + golden vectors | report worked example + confusion matrices | ☐ |
| M12-01 | Claim check against evidence (`POST /check` per-claim supported or not). | D4, G3 | 09 §3, 13 | P5 | services/critic/tests | challenge screenshot, blocked injections panel | ☐ |
| M12-02 | Injection defence: fetched text is data. | D4, G5 | 09 §3, 13 | P5 | services/critic/tests | challenge screenshot, blocked injections panel | ☐ |
| M12-03 | Input rail (user input validation/moderation/injection screening). | D4 | 09 §3, 13 | P5 | services/critic/tests | challenge screenshot, blocked injections panel | ☐ |
| M12-04 | Approval above budget. | D4 | 09 §3, 13 | P5 | services/critic/tests | challenge screenshot, blocked injections panel | ☐ |
| M12-05 | Keeps only Skeptic objections backed by evidence (Discover step 6, Validate step 6). | D3 | 09 §3, 13 | P5 | services/critic/tests | challenge screenshot, blocked injections panel | ☐ |
| M12-06 | Receives verdict from M11 (figure arrow) to check card claims before display. | F1 | 09 §3, 13 | P5 | services/critic/tests | challenge screenshot, blocked injections panel | ☐ |
| M13-01 | Tracing per run; every step/tool/model call writes a trace record. | D4, G7, G2.3 | 12 | P2,P9 | services/agentops/tests, behaviour tests, eval job | trace, dashboard, drift screenshots | ☐ |
| M13-02 | Replay command re-runs a stored run using recorded tool results. | G7 | 12 | P2,P9 | services/agentops/tests, behaviour tests, eval job | trace, dashboard, drift screenshots | ☐ |
| M13-03 | Budgets: tokens, pages, steps, cost per run. | D4, G7 | 12 | P2,P9 | services/agentops/tests, behaviour tests, eval job | trace, dashboard, drift screenshots | ☐ |
| M13-04 | Prompt and rubric registry: versioned files; each verdict records versions; change → new version + new evaluation. | D4, G7 | 12 | P2,P9 | services/agentops/tests, behaviour tests, eval job | trace, dashboard, drift screenshots | ☐ |
| M13-05 | Evaluation harness: labelled ideas → agreement + confusion matrix; behaviour checks (tools used, evidence cited, budget kept, injection resisted); runs as K8s Job and nightly from Jenkins. | D4, G7 | 12 | P2,P9 | services/agentops/tests, behaviour tests, eval job | trace, dashboard, drift screenshots | ☐ |
| M13-06 | Drift reports after each ingestion: freshness, niche coverage, verdict distribution shift. | D4, G7 | 12 | P2,P9 | services/agentops/tests, behaviour tests, eval job | trace, dashboard, drift screenshots | ☐ |
| M13-07 | Dashboard: cost per verdict, latency per agent, cache hit rate, fallbacks, approvals, blocked injections. | D4, G7 | 12 | P2,P9 | services/agentops/tests, behaviour tests, eval job | trace, dashboard, drift screenshots | ☐ |
| M13-08 | Behaviour tests with recorded responses: invalid JSON, tool timeout, empty source, poisoned page. | G7 | 12 | P2,P9 | services/agentops/tests, behaviour tests, eval job | trace, dashboard, drift screenshots | ☐ |
| M13-09 | `POST /trace`, `GET /runs/{id}/trace`; traces/*.jsonl on volume + traces_index. | G3, G-F1 | 12 | P2,P9 | services/agentops/tests, behaviour tests, eval job | trace, dashboard, drift screenshots | ☐ |
| M14-01 | GitHub repo created day 1, protected main (no direct push, PR only, required checks, 1 review). | D4, G4.2 | 15, 16 | P0,P1,P11 | GHA + Jenkins runs | CI/CD, pods, rollout/rollback screenshots | ☐ |
| M14-02 | One issue per task linked to PR; small single-purpose branches. | G4.2 | 15, 16 | P0,P1,P11 | GHA + Jenkins runs | CI/CD, pods, rollout/rollback screenshots | ☐ |
| M14-03 | Conventional commits. | G4.2 | 15, 16 | P0,P1,P11 | GHA + Jenkins runs | CI/CD, pods, rollout/rollback screenshots | ☐ |
| M14-04 | One deliberate merge conflict, resolved in a PR, screenshot kept. | G4.2, D6 | 15, 16 | P0,P1,P11 | GHA + Jenkins runs | CI/CD, pods, rollout/rollback screenshots | ☐ |
| M14-05 | GitHub Actions on every PR: lint+type, unit tests (incl. M11), UI component tests, retrieval quality gate, docker build, image scan, manifest validation; all required. | D4, G8.1 | 15, 16 | P0,P1,P11 | GHA + Jenkins runs | CI/CD, pods, rollout/rollback screenshots | ☐ |
| M14-06 | Jenkins: checkout, build+tag SHA, push, deploy to test cluster, smoke test, nightly eval vs baseline; failure stops, nothing deployed. | D4, G8.2 | 15, 16 | P0,P1,P11 | GHA + Jenkins runs | CI/CD, pods, rollout/rollback screenshots | ☐ |
| M14-07 | Jenkins in a container with Docker + kubeconfig as Jenkins credential; SCM polling or tunnel. | G8.2 | 15, 16 | P0,P1,P11 | GHA + Jenkins runs | CI/CD, pods, rollout/rollback screenshots | ☐ |
| M14-08 | One Docker image per module (≥ 6 distinct), multi-stage, non-root, pinned, .dockerignore, tagged by SHA, scanned. | D4, G4.4 | 15, 16 | P0,P1,P11 | GHA + Jenkins runs | CI/CD, pods, rollout/rollback screenshots | ☐ |
| M14-09 | Full K8s deployment with RollingUpdate (maxUnavailable 0), rollout and rollback demonstrated. | D4, G9 | 15, 16 | P0,P1,P11 | GHA + Jenkins runs | CI/CD, pods, rollout/rollback screenshots | ☐ |
| M14-10 | docker-compose for dev only; settings in env/ConfigMaps; `.env.example`; README with short command list. | G4.3 | 15, 16 | P0,P1,P11 | GHA + Jenkins runs | CI/CD, pods, rollout/rollback screenshots | ☐ |
| M14-11 | Local K8s whose CNI enforces NetworkPolicy. | G4.3, G9.3 | 15, 16 | P0,P1,P11 | GHA + Jenkins runs | CI/CD, pods, rollout/rollback screenshots | ☐ |
| DF-01 | All modules exchange JSON; each schema defined once (Pydantic/JSON Schema); validate every input/output; schema files in repo. | G2 | 04, schemas/ | P0 | libs/common schema drift test | schemas/ folder | ☐ |
| DF-02 | Idea card fields: id, name, summary, problem, customer, niche_tags, stage, funding_usd, source, url, published_at, fetched_at, text. | G2.1 | 04, schemas/ | P0 | libs/common schema drift test | schemas/ folder | ☐ |
| DF-03 | Profile fields: user_id, skills, hours_per_week, weeks_available, team_size, compute, goal, interests. | G2.1 | 04, schemas/ | P0 | libs/common schema drift test | schemas/ folder | ☐ |
| DF-04 | Evidence item: evidence_id, card_id, quote, source, url, retrieved_at. | G2.2 | 04, schemas/ | P0 | libs/common schema drift test | schemas/ folder | ☐ |
| DF-05 | Dimension score: dimension, score, confidence, evidence_ids, features, reason, rubric_version, model. | G2.2 | 04, schemas/ | P0 | libs/common schema drift test | schemas/ folder | ☐ |
| DF-06 | Verdict: idea_id, version, run_id, verdict, stability, score, confidence, gates, weights, thresholds, scores, weakest_dimension, what_would_make_go, sensitivity{runs,flipped}, versions{engine,rubrics,profile_weights}. | G2.3 | 04, schemas/ | P0 | libs/common schema drift test | schemas/ folder | ☐ |
| DF-07 | Trace: run_id, step_id, parent_step, agent, kind, tool, input, output, tokens_in, tokens_out, cost_usd, latency_ms, retries, model, cache_hit, error, ts. | G2.3 | 04, schemas/ | P0 | libs/common schema drift test | schemas/ folder | ☐ |
| DF-08 | Rubric file per dimension: dimension, version, scale, anchors 0–5 (every level), features, output_schema, ≥1 worked example per level. | G10 | 04, schemas/ | P0 | libs/common schema drift test | schemas/ folder | ☐ |
| NFR-01 | Every verdict traceable to scores → evidence ids → source URLs; versions recorded. | team/derived | 13, 14, 16 | all | see 14 | report | ☐ |
| NFR-02 | M11 pure function; identical input ⇒ byte-identical output (sorted keys, fixed rounding). | team/derived | 13, 14, 16 | all | see 14 | report | ☐ |
| NFR-03 | Timeouts on all outbound calls (default 10 s tool, 60 s LLM); retries with exponential back-off + jitter (max 2); circuit breaker on repeated source failure. | team/derived | 13, 14, 16 | all | see 14 | report | ☐ |
| NFR-04 | ≥2 replicas for gateway and verdict; RollingUpdate maxUnavailable 0; readiness gates traffic. | team/derived | 13, 14, 16 | all | see 14 | report | ☐ |
| NFR-05 | Validate cache hit < 1 s; cache miss p95 < 60 s; search p95 < 500 ms. | team/derived | 13, 14, 16 | all | see 14 | report | ☐ |
| NFR-06 | Per-run budget defaults in `config/budgets.yaml`; cost per verdict reported. | team/derived | 13, 14, 16 | all | see 14 | report | ☐ |
| NFR-07 | See `13_SECURITY.md` (auth, rate limits, input rail, injection defence, secrets, NetworkPolicy, non-root, scans). | team/derived | 13, 14, 16 | all | see 14 | report | ☐ |
| NFR-08 | No private data; only public text + public URL; no PII in traces beyond user_id. | team/derived | 13, 14, 16 | all | see 14 | report | ☐ |
| NFR-09 | JSON logs to stdout; traces JSONL to stdout + volume; `/metrics` on every service. | team/derived | 13, 14, 16 | all | see 14 | report | ☐ |
| NFR-10 | Runs on any laptop with Docker + kind; README brings it up with a short command list. | team/derived | 13, 14, 16 | all | see 14 | report | ☐ |
| NFR-11 | Layered code, typed (mypy), linted (ruff), small modules, schemas shared via `libs/common`. | team/derived | 13, 14, 16 | all | see 14 | report | ☐ |
| NFR-12 | Recorded-response replay so agent logic is testable without a model, fast and free. | team/derived | 13, 14, 16 | all | see 14 | report | ☐ |
| NFR-13 | Weights, thresholds, floors, budgets, rubric versions in ConfigMaps; no code change to tune. | team/derived | 13, 14, 16 | all | see 14 | report | ☐ |
| NFR-14 | Fine-tune settings, data splits, eval commands committed; seeds fixed; image tags = commit SHA. | team/derived | 13, 14, 16 | all | see 14 | report | ☐ |
| NFR-15 | Keyboard navigable, colour + text for verdict (not colour only), semantic HTML. | team/derived | 13, 14, 16 | all | see 14 | report | ☐ |

Total requirements: **167**.
