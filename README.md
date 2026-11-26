# Idea Generator with Go / No-Go
CS4032 Software Construction and Development, FAST-NUCES. Semester project (group of 2).

An interactive multi-agent system that helps a student choose a project. It collects startup ideas and products from
public sources (Hacker News, GitHub, Reddit / Product Hunt), organises them into a map of niches and trends, and works in two modes:
- **Discover**: give your profile, get ideas that fit, including new ideas built from gaps in the data.
- **Validate**: bring an idea and get **GO / PIVOT / NO-GO / INSUFFICIENT EVIDENCE** with evidence, what would change it, and how stable it is.

> The language model never decides the verdict. Models produce evidence-backed scores; a deterministic, unit-tested
> engine (M11) turns them into the verdict. Decision support, not a prediction of success.

![architecture](docs/harness/figure1_architecture_from_pdf.png)

## This repository currently contains the **build harness**
Everything a coding agent (Claude Code, Codex) or a human needs to build the system autonomously:
| Start here | |
|---|---|
| `CLAUDE.md` / `AGENTS.md` | Operating manual for coding agents: rules, loop, stack, commands |
| `PROGRESS.md` | Live project state, gate log, blockers |
| `docs/harness/00_INDEX.md` | Map of all specs: PRD, requirements, architecture, data model, API, flows, state machines, verdict engine, agents, RAG, fine-tuning, AgenticOps, security, testing, CI/CD, Kubernetes, plan, critical path, gates, backlog, UI, deliverables, risks, don'ts, traceability, assumptions, glossary, tech stack |
| `docs/source/` | Verbatim text of the instructor's two PDFs |
| `config/`, `rubrics/`, `schemas/`, `prompts/`, `eval/` | Seed artefacts (rules, rubrics, JSON contracts, prompts, eval sets, verdict test vectors) |

### How to use the harness with a coding agent
1. Create the GitHub repo, copy this folder in, push (owner does branch protection: see `docs/harness/15_CICD.md`).
2. Fill team info + get API keys (see `PROGRESS.md` → Blocked / Needs human). Put keys in a local `.env` (never commit).
3. Open the repo in Claude Code (or Codex) and say: **"Read CLAUDE.md and PROGRESS.md, then run /next-task."**
   Each developer tells their agent which role they are (Dev A = Taimoor or Dev B = Bilal, see `18_CRITICAL_PATH_AND_PARALLELIZATION.md`).
4. After each phase: **`/phase-gate <N>`** (testing + security gate). Capture the phase's screenshots.
5. Review every PR yourselves (the other member approves). Keep `docs/ai-usage-log.md` current.

## Quick start (fill in as the system is built; must stay ≤ ~10 commands)
```bash
cp .env.example .env            # fill values locally
make setup                      # uv + npm deps, pre-commit
make k8s-up                     # kind + Calico + registry + ingress + metrics-server
./scripts/create_secrets.sh     # K8s Secrets from .env
make images TAG=$(git rev-parse --short=7 HEAD) && make deploy TAG=$(git rev-parse --short=7 HEAD)
make seed                       # restore card snapshot (no internet needed)
make smoke                      # health + a validate run
open http://localhost/          # UI through the Ingress
```

## Team
| Member | Roll no. | Role |
|---|---|---|
| Taimoor (`T361`) | 24i-3015 | Dev A: Platform, Data, Ops & UI |
| Bilal (`IfBilal`) | 24i-3166 | Dev B: Decision, Agents & Analysis |
