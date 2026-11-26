# 01 — Product Requirements Document (PRD)

## 1. Product summary
**Idea Generator with Go / No-Go** helps a student choose a project. It collects startup ideas, products and
niches from public sources (YC directory, Hacker News, GitHub, Product Hunt or Reddit), organises them into a
**map of niches and trends**, and works in two modes:

- **Discover**: the student gives a profile (skills, hours, team size, compute, goal: startup / portfolio /
  course project). The system proposes ideas and niches that fit, **including new ideas built from gaps** it found.
- **Validate**: the student brings an idea (or picks a proposed one). The system returns a verdict:
  **GO, PIVOT, NO-GO or INSUFFICIENT EVIDENCE**, with the evidence behind it, **what would change it**, and
  **how stable it is**.

It is a complete product: web interface, multi-agent backend, knowledge base (RAG), a fine-tuned scoring model,
monitoring and evaluation of the agents (AgenticOps), and a delivery pipeline that builds, tests and deploys
everything on Kubernetes. It is **decision support, not a prediction of success**.

## 2. Users and personas
| Persona | Description | Key need |
|---|---|---|
| Solo student (course project) | 20 free hours, laptop only | Something that fits the hours; honest NO-GO for big ideas |
| Team of 2 (portfolio) | ~10 h/week × 12 weeks each, free GPU | Ideas that teach target skills and look good on a CV |
| Startup-minded student | 200+ free hours, team | Demand evidence, competition room, differentiation |
| Instructor / marker (secondary) | Opens repo, report, screenshots | Can start the system from README, sees every requirement evidenced |

Key product truth: **the same idea can get different verdicts for different students.** A large idea can be GO
for a team with 200 free hours and NO-GO for a solo student with 20. Profile drives weights (goal) and the
feasibility gate (hours).

## 3. User stories and acceptance (verbatim "Done when" from Description §2)

| # | The user can | Done when | Primary modules |
|---|---|---|---|
| U1 | Sign up, log in and save a profile | The profile is stored, editable, and **changes later recommendations** | M1, M2 |
| U2 | Ask for ideas in a niche or in general (Discover) | A **ranked list** of ideas is returned, each with a **verdict and a short reason** | M1, M3, M7, M8, M9–M12 |
| U3 | Watch the agents work live | The screen shows the **running agent, its tool calls and its spend** as they happen | M1, M3, M13 |
| U4 | Approve or decline an expensive deep search | The system **states the cost and waits** before going over budget | M1, M3, M12, M13 |
| U5 | Submit their own idea (Validate) and answer clarifying questions | **Only questions that change the result** are asked, then a verdict card is shown | M1, M3, M11 |
| U6 | Read a verdict card | It shows the **verdict, gates, six scores with evidence links, weights, confidence and stability** | M1, M11 |
| U7 | Ask "what would make this GO?" | Concrete changes are proposed and **one can be applied, giving version 2 computed by the engine** | M1, M3, M8, M9, M11 |
| U8 | Press "Challenge" on a claim | The critic **re-checks it against its evidence and defends or removes it** | M1, M12, M11 |
| U9 | Compare ideas and see history | Past ideas and versions are listed and **two ideas compare on all six dimensions** | M1, M2 |
| U10 | See why a verdict is INSUFFICIENT EVIDENCE | The card **says what was missing and does not guess** | M1, M11 |

### Expanded acceptance criteria (testable)
- **U1**: register → login → `PUT /profile` persists; profile has `version` that increments on change; running the
  same Validate after changing `hours_per_week` or `goal` yields a different cache key and can yield a different verdict.
- **U2**: `POST /discover` with or without `niche`; result is sorted by verdict rank (GO > PIVOT > INSUFFICIENT_EVIDENCE > NO-GO)
  then by weighted score; each item has `verdict`, `stability`, `score`, `reason` (≤ 2 sentences citing gap evidence).
- **U3**: SSE stream `/runs/{id}/events` emits `agent_started`, `tool_call`, `llm_call`, `spend_update` within ≤1 s
  of the underlying trace record; UI shows current agent, list of tool calls, running cost.
- **U4**: if the next step's estimated cost would push `spent + estimate > budget`, the run enters
  `AWAITING_APPROVAL`, emits `approval_required {estimated_cost_usd, budget_usd, spent_usd, action}`, and does nothing
  until `POST /runs/{id}/approve {decision}`. Decline → run continues without that step (or ends with what it has).
- **U5**: intake lists missing/uncertain inputs; a question is asked **only if** answering it at its plausible extremes
  produces different verdicts (value-of-information check via M11); max 3 questions; then a verdict card.
- **U6**: card shows verdict + stability badge, the 4 gates with pass/fail + reason, the six scores with confidence and
  clickable evidence links (to source URL), the weights used, thresholds, overall confidence, sensitivity summary
  (`runs`, `flipped`, what flips it), versions (engine, rubrics, weights), cache-hit indicator.
- **U7**: "What would make this GO?" lists ≥1 concrete change (text + target dimension + predicted new score/verdict);
  "Apply" creates `idea_version = n+1`, re-runs scorers and engine, and shows v2 side-by-side with v1.
- **U8**: "Challenge" on a claim → M12 re-checks the claim against its evidence ids → `defended` (with quote) or
  `removed` (claim and its evidence removed, scores recomputed by M11 → new version, reason shown).
- **U9**: `/ideas` lists ideas with latest verdict; `/ideas/{id}/versions` lists versions; `/compare?a=&b=` shows all
  six dimensions side by side (score, confidence, evidence count) + verdicts.
- **U10**: when INSUFFICIENT_EVIDENCE, card lists exactly what was missing (e.g. "2 of 3 required independent sources,
  1 of 2 platforms", or "confidence 0.42 < 0.50; low dimensions: competition (0.2)", or "risk scorer abstained: no evidence")
  and shows **no score-based verdict**.

## 4. Goals and non-goals
**Goals**: every requirement in both PDFs met and evidenced; explainable, repeatable verdicts; working end-to-end
deployment on Kubernetes; measurable retrieval, fine-tuning and agent quality.

**Non-goals**: predicting startup success; scraping private/logged-in content; production-scale traffic;
a model smarter than hosted ones (the fine-tune goal is cheaper, faster, more consistent rubric-following).

## 5. Success metrics (we report these)
| Metric | Target |
|---|---|
| Cards in KB | ≥ 1,500 from ≥ 3 sources (goal: 2,500 from 4) |
| Retrieval quality on 20+ hand queries | Recall@5 ≥ 0.75, MRR ≥ 0.6 (baseline stored; CI fails if it drops) |
| Fine-tuned model valid-JSON rate | ≥ 95% (after one retry ≥ 99%) |
| Fine-tuned MAE vs human on hand-checked test | ≤ hosted-model MAE + 0.3; within-1-point share ≥ 80% |
| Verdict agreement with labelled set (after tuning) | ≥ 70% exact; **false-GO (system GO, human NO-GO) reported separately, target 0** |
| Behaviour tests | invalid JSON, tool timeout, empty source, poisoned page all pass |
| Cost per verdict | reported on dashboard; target < $0.02 with fine-tuned scorer |
| p95 Validate latency | reported; target < 60 s (cache miss), < 1 s (cache hit) |
| Deployment | all pods Running/Ready; rollout + rollback demonstrated; NetworkPolicy test passes |

## 6. Constraints
- 2 students, ~1 week. Both commit, both review, both can explain the whole system.
- AI tools allowed; humans responsible for every line; AI-usage log required.
- Official APIs / allowed pages only, robots.txt, rate limits, no private data, never commit keys.
- Whole system on Kubernetes, one container per module.

## 7. Release scope (what "done" means for submission)
See `23_DELIVERABLES_AND_REPORT.md`: repo, report (≤10 pages + screenshots), screenshots list, AI-usage log,
≥3 ADRs, signed contribution table matching git history.
