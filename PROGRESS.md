# PROGRESS.md — Live Project State

> Agents: read this second (after CLAUDE.md) at the start of every session and update it at the end of every task.
> Humans: this is the single place to see where the project is.

## Status
| Field | Value |
|---|---|
| Current phase | **Phase 0: Bootstrap & contracts** |
| Current task | P0-T01 (not started) |
| Last green main commit | — |
| Last Jenkins deploy | — |
| Cards in KB | 0 / 1,500 (sources 0 / 3) |
| Retrieval baseline (Recall@5 / MRR) | — |
| Fine-tune examples (total / hand-checked) | 0 / 0 |
| Labelled verdict set | 0 |

## Team
| Role | Name | Roll no. | GitHub | Owns |
|---|---|---|---|---|
| Dev A (Platform, Data, Ops & UI) | Taimur | 24i-3015 | T361 | M14, M5, M4, M6i, M6, M1 UI, M10t, M10 (serving), M13 (ops) |
| Dev B (Decision, Agents & Analysis) | Bilal | 24i-3166 | IfBilal | M11, M2, M1 gateway, M3, M9, M8, M7, M12, M13 (eval) |

## Current task plan
_(agent writes a 3–10 line plan here before starting a task: files to touch, tests to add, done criteria)_

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

## Blocked / Needs human
- [ ] Create GitHub repo + branch protection (owner action) (P0-T01)
- [ ] LLM provider API key → local `.env` (never commit)
- [ ] GitHub token; Reddit app credentials; Product Hunt developer token (request on Day 1)
- [ ] Decide which laptop hosts kind + Jenkins (≥ 16 GB RAM)
- [ ] Colab / Kaggle account for fine-tuning
- [ ] Ask instructor the open questions in `docs/harness/27_ASSUMPTIONS_AND_OPEN_QUESTIONS.md` §B

## Decisions made during build (link ADRs)
- ADR-001..007 (initial) in `docs/decisions/`
- Ownership rebalance (before Phase 0): UI moved Dev B → Dev A; M7, M12, M8 Skeptic/Change Suggester moved Dev A → Dev B (see 18 §1, 27 §A).

## Lessons / notes for future sessions
_(append short notes: gotchas, commands that worked, things to avoid)_
