# 27 — Assumptions (interpretations) and Open Questions

The PDFs leave some details open. Below is how we interpret each, so agents behave consistently. If the instructor
answers differently, update this file, the affected spec and an ADR. Report these under "changes from the architecture".

## A. Assumptions
| # | Topic | PDF says | Our interpretation | Where |
|---|---|---|---|---|
| A-01 | 4 scorers vs 6 dimensions | M9 has 4 scorers (Demand&Pain, Competition&Differentiation, Feasibility for this student, Risk); §5.1 has 6 dimensions incl. Learning | Competition&Differentiation scorer outputs 2 dims; Feasibility-for-this-student scorer also outputs Learning & portfolio value (both depend on the student's profile) | ADR-004, 09 §5 |
| A-02 | "Independent source" | evidence gate: ≥3 independent sources on ≥2 platforms | source = distinct card (after URL + near-duplicate dedupe) cited as relevant evidence; platform = `card_sources.platform`. `sources` in confidence formula = same count | 08 §1, 09 §4 |
| A-03 | Hosted LLM and NetworkPolicy | only M5 reaches the internet; M10 falls back to a hosted model; agents use LLMs | all hosted-model traffic goes through an M5 LLM proxy (LiteLLM) | ADR-003 |
| A-04 | Sensitivity scope | Figure 4 shows sensitivity after the GO branch; §5.3 describes it generally; Guide example has `sensitivity` on the verdict | run the 14-run sensitivity for every verdict decided at the scoring stage (GO/PIVOT/NO-GO by score); gate/abstention/low-confidence verdicts report `runs: 0` | 08 §2 |
| A-05 | Sensitivity run count | "each weight moved up and down by 20 percent … and the least confident score moved by one point" | 6×2 + 2 = 14 runs (matches Guide example `"runs": 14`); "20 percent" is relative (25→20, as in the worked example) | 08 |
| A-06 | Challenge confidence cap | "lowers the matching score by 0.5 (at most 1.0) and its confidence by 0.1" | cap −1.0 applies to score; confidence −0.1 per accepted objection, clamped at 0 | 08 §2 |
| A-07 | Validate step order | Figure 3: Scorers (3) then Features (4) | Features computed before scorers consume them; trace keeps both step names | 06 §2 |
| A-08 | Abstained dimension | "otherwise the scorer abstains"; IE "does not guess" | any abstained dimension → INSUFFICIENT_EVIDENCE naming it (after gates) | 08 |
| A-09 | Floor + low score | "A score between T_no and T_go, or a floor breach, gives PIVOT"; Figure 4 routes floor breach through "Score ≥ T_no?" | floor breach with S ≥ T_no → PIVOT; with S < T_no → NO-GO (follows the figure) | 08 |
| A-10 | Available hours | "fits the student's hours" | available = hours_per_week × weeks_available × team_size | 08 |
| A-11 | Threshold inclusivity | "at least T_go", "Below T_no is NO-GO" | S ≥ T_go GO; S ≥ T_no PIVOT; floor breach = strictly below 1.5 | 08 |
| A-12 | What-if search | "smallest change that changes the verdict" | single-dimension increases in 0.5 steps; prefer better verdict, then stable; plus gate-specific suggestions; M8 turns deltas into concrete text | 08 §2 |
| A-13 | M2 "Layered backend as in Assignment 1" | refers to a previous assignment | routes → services → repositories, enforced with import-linter. **Team: check your Assignment 1 structure and mirror its naming** | 21 §2 |
| A-14 | Separate M2 service | repo layout lists no `profile` service | M2 is its own `profile` service (allowed: "You may group services differently") | 03 |
| A-15 | M8+M9 deployment | Guide 9.1 groups "M8 and M9" | separate Deployments by default; merging is a documented fallback | 03, 24 |
| A-16 | Labelled verdict set size | not specified | ≥ 40 ideas (≥ 10 clear GO, ≥ 10 clear NO-GO, ≥ 10 borderline, ≥ 10 thin-evidence) | 17 P10 |
| A-17 | Rate limits numbers | not specified | 100 req/min/IP, 10 runs/min/user, login 5/min/IP | 13 |
| A-18 | Default budgets | not specified | `config/budgets.yaml` values (validate $0.05, discover $0.25) | 12 |
| A-19 | "Ranked list" order | not specified | verdict rank, then score, then confidence | 07 §3 |
| A-20 | Debate early stop | "repeat up to 3 rounds" | stop early when no new accepted non-fatal objections or enough survivors | 07 §3 |
| A-21 | Team ownership split | Guide asks for a contribution table, no split given | Dev A (Taimur): platform, data, ops, UI, fine-tuning/serving. Dev B (Bilal): verdict, agents, orchestrator, gateway, M7, M12. Chosen so every Validate-path producer/consumer is one developer's and the UI is mock-driven in parallel | 18, 20 |

## B. Open questions (ask the instructor early; proceed with the assumption meanwhile)
1. Is it acceptable that Learning & portfolio value is produced by the Feasibility-for-this-student scorer (A-01)?
2. Does the hosted LLM proxy in M5 satisfy "only M5 reaches the internet" as intended (A-03)?
3. Which "Assignment 1" layering conventions should M2 mirror exactly (A-13)?
4. Must the repo be public, or is adding the instructor as collaborator enough?
5. Is a 1B model (Llama-3.2-1B) acceptable if Colab limits block 3B (yes per "1B to 3B", confirming)?
6. Expected minimum size of the labelled verdict set?

## C. Team inputs needed (agents: write to PROGRESS.md "Blocked / Needs human" until provided)
- Team (known): Dev A = Taimur (`T361`, 24i-3015), Dev B = Bilal (`IfBilal`, 24i-3166).
- LLM provider + API key (put in local `.env` → K8s Secret).
- GitHub token, Reddit app credentials, Product Hunt token.
- Which laptop runs the cluster + Jenkins (RAM ≥ 16 GB recommended).
- Colab/Kaggle accounts for training.
