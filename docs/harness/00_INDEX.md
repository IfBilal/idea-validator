# 00 — Harness Index

This harness turns the two instructor PDFs (verbatim text in `docs/source/`, figures copied as PNGs next to
this file) into everything a coding agent needs to build, test, secure, deploy and document the project.

## Source of truth hierarchy
1. `docs/source/Project_Description.txt` = **what to build** (instructor).
2. `docs/source/Project_Guide.txt` = **how to build it** (instructor). Field lists and numbers are *minimums*.
3. This harness = our interpretation, decisions and plan. If the harness contradicts 1 or 2, **the PDFs win**;
   fix the harness and log it in `27_ASSUMPTIONS_AND_OPEN_QUESTIONS.md`.

## Documents

| # | File | Read when |
|---|---|---|
| — | `../../CLAUDE.md` / `AGENTS.md` | Always, first |
| — | `../../PROGRESS.md` | Always, second (live state) |
| 01 | `01_PRD.md` | Understanding the product, users, stories U1–U10, success metrics |
| 02 | `02_REQUIREMENTS.md` | Every functional (FR) and non-functional (NFR) requirement with IDs |
| 03 | `03_ARCHITECTURE.md` | Module boundaries, communication, tech stack, deployment topology |
| 04 | `04_DATA_MODEL.md` | Postgres tables, KB records, Redis keys, files on volumes |
| 05 | `05_API_CONTRACTS.md` | Every endpoint (public + internal), request/response, SSE events, errors |
| 06 | `06_DATA_FLOW.md` | Sequence diagrams: Discover, Validate, Challenge, What-if, Approval, Scouting, Ingestion |
| 07 | `07_WORKFLOWS_STATE_MACHINES.md` | M3 state machines, retries, timeouts, budgets, re-run-only-what-changed |
| 08 | `08_VERDICT_ENGINE_SPEC.md` | M11 algorithm, exact formulas, sensitivity, what-if, test vectors |
| 09 | `09_AGENTS_PROMPTS_RUBRICS.md` | Every agent (Ideator, Gap Analyst, Skeptic, 4 scorers, Critic), tools, schemas, rubric rules |
| 10 | `10_RAG_KNOWLEDGE_BASE.md` | M4 scouts, M5 tool layer, M6i ingestion, M6 hybrid search, M7 niches/gaps, retrieval eval |
| 11 | `11_FINE_TUNING.md` | M10t dataset, training, merge/quantise, M10 serving, fallback, comparison |
| 12 | `12_AGENTICOPS.md` | M13 tracing, replay, budgets, registry, eval harness, drift, dashboard, behaviour tests |
| 13 | `13_SECURITY.md` | Threat model, guardrails, injection defence, auth, secrets, NetworkPolicy, supply chain |
| 14 | `14_TESTING_STRATEGY.md` | Test pyramid, per-module required tests, replay/behaviour tests, coverage targets |
| 15 | `15_CICD.md` | Git workflow, branch protection, GitHub Actions, Jenkins, merge-conflict exercise |
| 16 | `16_KUBERNETES.md` | All K8s objects, manifests, probes, HPA, NetworkPolicy test, rollout/rollback, measurement |
| 17 | `17_IMPLEMENTATION_PLAN.md` | **Phases 0–12** with tasks, outputs, exit criteria |
| 18 | `18_CRITICAL_PATH_AND_PARALLELIZATION.md` | Dependency graph, critical path, Dev A (Taimoor) / Dev B (Bilal) split, day-by-day |
| 19 | `19_QUALITY_GATES.md` | Testing gate + security gate for every phase (checklists + commands) |
| 20 | `20_TASK_BACKLOG.md` | Every task as a GitHub-issue-ready item (ID, owner, deps, acceptance) |
| 21 | `21_CODING_STANDARDS.md` | Layering, style, errors, logging, config, naming, commit rules |
| 22 | `22_UI_UX_SPEC.md` | Screens, components, verdict card layout, live run view, states |
| 23 | `23_DELIVERABLES_AND_REPORT.md` | Submission checklist, report outline (≤10 pages), screenshot list |
| 24 | `24_RISK_REGISTER.md` | Project risks, mitigations, fallbacks, what to cut if late |
| 25 | `25_DONTS_AND_COMMON_MISTAKES.md` | Everything that must NOT be done (instructor's list + ours) |
| 26 | `26_REQUIREMENTS_TRACEABILITY_MATRIX.md` | Every PDF requirement → spec → phase → test → evidence |
| 27 | `27_ASSUMPTIONS_AND_OPEN_QUESTIONS.md` | Interpretations of ambiguous text, questions for the instructor/team |
| 28 | `28_GLOSSARY.md` | Terms |
| 29 | `29_TECH_STACK.md` | Modern stack with versions policy and rationale (LangGraph, PydanticAI, LiteLLM, Qdrant, Unsloth, React 19 …) |

## Seed artefacts already in the repo
- `config/verdict.yaml`, `config/budgets.yaml`, `config/sources.yaml`: starting values from the PDFs.
- `rubrics/*.yaml`: six rubric files (anchors 0–5, features, output schema, example slots).
- `schemas/*.schema.json`: JSON Schemas for every record in Guide §2.
- `prompts/*.md`: versioned prompt skeletons for each agent.
- `eval/verdict/test_vectors.json`: M11 test vectors including the PDF worked example.
- `eval/retrieval/queries.yaml`: 20+ hand-written retrieval queries (fill expected card ids after ingestion).
- `eval/labelled/README.md`: how to build the labelled verdict set.
- `docs/decisions/`: ADR template + initial ADRs.
- `docs/ai-usage-log.md`, `docs/contribution.md`, `docs/report/REPORT_OUTLINE.md`, `docs/screenshots/CHECKLIST.md`.
- `.github/`: PR template, issue templates, `pr-checks.yml` starter.
- `Jenkinsfile`, `Makefile`, `.env.example`, `.gitignore`, `.dockerignore.template`, `.pre-commit-config.yaml`.
- `.claude/`: settings (permissions), slash commands, subagents.

## Figures from the PDFs (look at them, they contain requirements)
- `figure1_architecture_from_pdf.png`: high-level architecture (arrows = allowed calls).
- `figure2_3_workflows_from_pdf.png`: Discover + Validate workflows (step → module mapping).
- `figure4_verdict_flow_from_pdf.png`: M11 decision flow.
- `guide_fig1_data_model_from_pdf.png`: data model.
- `guide_fig2_cicd_from_pdf.png`: CI/CD.
- `guide_fig3_k8s_from_pdf.png`: Kubernetes deployment.
