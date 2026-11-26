# 18 — Critical Path, Dependencies and Parallel Work

## 1. Ownership (both members still review each other's PRs and must explain everything)
Dev A = **Taimoor**. Dev B = **Bilal**.

| Dev A: Platform, Data, Ops & UI (Taimoor) | Dev B: Decision, Agents & Analysis (Bilal) |
|---|---|
| M14 Platform (repo, CI, Jenkins, Docker, K8s, NetworkPolicy) | M11 Verdict Engine |
| M5 Tool Layer (incl. LLM proxy) | M2 Profile, Session & Cache; M1 Gateway (API, auth, SSE; not the browser UI) |
| M4 Scouts, M6i Ingestion, M6 KB, retrieval eval | M3 Orchestrator (LangGraph) |
| **M1 UI (all screens, React app, component + E2E tests)** | M9 Validation agents (+ M10 wrapper contract) |
| M10t Fine-tuning + M10 serving | M8 Ideator, Gap Analyst, Skeptic, Change Suggester, Idea Rewriter; Discover |
| M13 traces, replay, drift, dashboard | **M7 Niche & Trend Analyst (niches, gaps, features)** |
| | **M12 Critic & Guardrails** |
| | M13 eval harness; verdict tuning |
Shared: labelled sets (retrieval queries, fine-tune hand checks, verdict labels), merge-conflict exercise, screenshots, report.

Why this split: B owns every producer and consumer on the Validate path (M7 features → M9 scorers → M12 critic → M3 → M11),
so M3 never waits on a cross-developer handoff. A owns the UI, which only needs `05_API_CONTRACTS.md`, `schemas/examples/*.json`
and MSW mocks, so it runs in parallel with B's backend. B owes A the gateway contract and the SSE event format (frozen D4).

## 2. Module dependency graph
```mermaid
flowchart LR
  S[schemas + libs/common] --> M11 & M2 & M5 & SK[service template]
  SK --> K8S[K8s skeleton + Jenkins + NetPol]
  M5 --> M4 --> M6i --> M6
  M6 --> M7
  M6 --> M12
  M6 --> RQ[retrieval eval gate]
  M7 --> FEAT[features]
  M2 --> GW[gateway auth/profile]
  M11 --> M3V[M3 Validate]
  FEAT --> M9
  M5 --> M10w[M10 wrapper hosted mode] --> M9
  M9 --> M3V
  M12 --> M3V
  M8s[M8 Skeptic] --> M3V
  M3V --> M3D[M3 Discover]
  M8i[M8 Ideator/Gap] --> M3D
  M7 --> M8i
  M3V --> UI[UI cards]
  M3D --> UI
  M6 --> FT[fine-tune data] --> TRAIN[train on Colab] --> M10[M10 served] --> CMP[comparison]
  M3V --> EVAL[eval harness] --> TUNE[verdict tuning] --> REPORT
  K8S --> MEAS[measure requests/limits] --> REPORT
```

## 3. Critical path (longest chain, protect it)
```
schemas/libs (D1) → M5 tool layer (D2) → scouts (D2) → ingestion ≥1,500 cards (D3 AM) → M6 hybrid search (D3)
→ M7 features + gaps (D3 PM) → M9 scorers (D4 AM) → M3 Validate graph (D4) → M3 Discover (D5)
→ eval harness + labelled set (D5–D6) → verdict tuning before/after (D6) → screenshots (D6–D7) → report (D7)
```
Second chain (parallel, must not slip): `KB (D3) → fine-tune candidates (D3) → labels + hand-check (D3–D4) → Colab
training (D4, overnight) → M10 served + comparison (D5)`.

**Slack:** M11 (pure code) has lots of slack. Build it D2 so M3 never waits, then help on UI.
**Risks on the critical path:** API keys for Reddit/PH (request on D1!), scout rate limits (start scouting D2 morning and
let CronJobs run overnight), Colab GPU availability (have Kaggle as backup), laptop RAM (see 24).

## 4. What can run in parallel (and what must not)
| Can be parallel | Why it's safe |
|---|---|
| M11 (B) ‖ M5 + scouts (A) | M11 depends only on schemas |
| M2/gateway auth (B) ‖ ingestion (A) | Different tables/services; schemas fixed in P0 |
| UI shell & components with MSW mocks (A) ‖ backend agents (B) | Contracts in 05 + schemas; MSW mocks from examples |
| Fine-tune data/training (A) ‖ M3 graphs (B) | M9 uses M10 wrapper contract in hosted mode until model is ready |
| M7 + M12 + Skeptic ‖ M9 scorers (all B, sequenced by B) | All consume M6; produce independent schemas; no cross-developer wait |
| Drift/dashboard + UI pages (A) ‖ Discover (B) | Trace schema fixed |

| Must be sequential | Because |
|---|---|
| Schemas → anything that exchanges JSON | contract first |
| NetworkPolicy + M5 → any scout/LLM call in cluster | only M5 may reach internet |
| Ingestion → retrieval labelling → retrieval gate | labels need real card ids |
| KB → fine-tune candidates | examples come from the KB |
| Labelled verdict set (draft) → fine-tune split | must exclude labelled ideas from training |
| M9 + M11 + M12 → M3 Validate → M3 Discover | Discover validates survivors |
| Eval harness + labelled set → tuning → final ConfigMap → final screenshots | screenshots show final behaviour |
| Final images → requests/limits measurement → final manifests | limits come from measurement |

## 5. Day-by-day (both developers)
| Day | Dev A | Dev B | Sync / joint |
|---|---|---|---|
| D1 | P0 repo/protection/CI/compose; P1 kind+Calico+registry, Kustomize, NetPol, Jenkins; UI shell (P1-T03) once the template exists | P0 schemas/libs; P1 service template, 13 stubs, stub validate path | Merge-conflict exercise; request API keys; gate P0, P1 |
| D2 | M5 complete (allow-list, robots, sanitiser, LLM proxy); scouts HN + GitHub; start CronJobs | M11 complete + golden tests; M2 migrations, repos, auth, profile, cache | Gate P2; start overnight scouting |
| D3 | Ingestion ≥1,500, snapshot, M6 hybrid search, retrieval labels+gate; fine-tune candidates; UI auth/profile/live panel with MSW | M7 niches/gaps/features; rubrics v1, M10 wrapper (hosted), M9 scorers | Label retrieval queries together (1 h); gate P3, P4 |
| D4 | UI clarify/approval/verdict card against example JSON; hand-check labels; start Colab training | M12 critic + rail; M8 Skeptic/Change Suggester; M3 Validate graph, gateway run endpoints + SSE (events frozen); behaviour tests | Hand-check fine-tune labels together; gate P5 |
| D5 | M10 served + comparison; M13 trace store, replay, drift, dashboard; UI discover page, what-if/compare/trace | M8 Ideator/Gap; M3 Discover; eval harness | Draft labelled verdict set together; gate P6–P9 |
| D6 | Requests/limits measurement, HPA, rollout/rollback; UI polish + Playwright E2E; screenshots | Before eval → tuning → after eval; ADR | Gate P10; fresh-clone test |
| D7 | Screenshots finish; report: platform, KB, fine-tuning, AgenticOps | Report: summary, verdict design, tuning, limits | Contribution table + sign; submit; gate P11–P12 |

## 6. Interfaces that unblock the other developer (deliver early)
- P0: all Pydantic schemas + example JSON payloads (`schemas/examples/*.json`) → used by MSW mocks and stub services.
- D2 AM: M5 `/llm` working → B's agents can call models.
- D3 AM: M6 `/search` on snapshot → B's M7, scorers and M12 get real evidence.
- D2 PM: M11 `/verdict` deployed → A's eval/drift work can call it.
- D4: M3 events format + gateway run endpoints frozen (B) → A's UI live panel.
