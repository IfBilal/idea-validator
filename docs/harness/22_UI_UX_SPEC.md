# 22 — UI / UX Specification (M1)

The browser talks **only** to the gateway (`API_BASE_URL` from runtime `/config.js`). Never to models/scouts/internal services.

## 1. Screens & routes
| Route | Screen | Stories |
|---|---|---|
| `/login`, `/register` | Auth forms (email, password; errors generic) | U1 |
| `/profile` | Profile form: skills (tag input), hours/week, weeks available, team size, compute (laptop/free_gpu/paid), goal (startup/portfolio/course_project), interests; shows profile version + "changes affect future recommendations" note | U1 |
| `/discover` | Niche input (optional) + count → Run → **Live run panel** + streaming results; ranked list cards (title, niche, verdict badge, stability, score, 1–2 sentence reason, gap evidence links); "Dropped by Skeptic" collapsible list | U2, U3 |
| `/validate` | Chat-style: idea textarea → Run → live panel → clarifying question cards (≤ 3) → approval dialog if needed → **Verdict card** | U3, U4, U5, U6 |
| `/ideas` | History table (title, source own/generated, latest verdict, score, updated) | U9 |
| `/ideas/:id` | Versions timeline (v1, v2 …, change notes) + verdict card for selected version + diff with previous | U7, U9 |
| `/compare?a=&b=` | Two-column compare: 6 dimensions (score, confidence, #evidence, delta) + verdicts + radar chart | U9 |
| `/runs/:id/trace` | Trace tree (agent → tool/llm/decision), cost, latency, cache hits | U3 (debug), screenshot |

## 2. Live run panel (U3)
- Header: run kind, state (Running / Waiting for your answer / Waiting for approval / Done), elapsed time.
- **Current agent** (highlighted, e.g. "Competition scorer"), step list with status icons, debate round indicator (Discover).
- **Tool calls** feed: tool name, short input, latency, cache-hit badge, error badge.
- **Spend meter**: `$0.012 / $0.05` + tokens + pages; turns amber at 80%.
- Reconnects automatically (SSE `Last-Event-ID`).

## 3. Approval dialog (U4)
"Deep search would cost about **$0.03** (spent $0.04 of $0.05 budget). It fetches ~20 more pages from GitHub and Reddit to
find more evidence." Buttons: **Approve** / **Decline** (decline explains the run continues with current evidence).
The system does nothing until the user chooses.

## 4. Clarifying question card (U5)
Question text + "Why we ask: your answer changes the verdict (GO if ≤ 150 h, NO-GO if 300 h)" + options / free text.
Max 3 questions; skipped questions use conservative defaults, listed on the card.

## 5. Verdict card (U6, U7, U8, U10) (component `VerdictCard`)
```
┌──────────────────────────────────────────────────────────────────────┐
│ [GO] [BORDERLINE]   Score 70.5 / 100   Confidence 0.83   v2  ⓘcached │
│ "Decision support, not a prediction of success."                      │
├── Gates ─────────────────────────────────────────────────────────────┤
│ ✔ Evidence (4 sources, 2 platforms)  ✔ Feasibility (180 h ≤ 240 h)   │
│ ✔ Risk (no blocker)                   ✔ Duplicate (none)              │
├── Six dimensions ────────────────────────────────────────────────────┤
│ Demand & pain   3.5 ████░ conf 0.8  w15  [card_8f21#c2↗][card_77a1#c0↗]│
│ Competition     2.0 ██░░░ conf 0.6  w15  … (−0.5 challenge) [Challenge]│
│ … differentiation, feasibility, risk, learning                         │
│   each reason sentence = a claim with evidence links + [Challenge]     │
├── Stability ─────────────────────────────────────────────────────────┤
│ 4 of 14 checks flip the verdict: weight feasibility −20% → PIVOT 69.9 │
├── What would make this GO? ──────────────────────────────────────────┤
│ • Narrow to a less crowded segment (competition 2.0→3.0) → 73.5 GO,   │
│   stable  [Apply → creates v3]                                        │
├── Weights & thresholds · versions (engine m11@v1, rubrics r1, w1) ───┤
└──────────────────────────────────────────────────────────────────────┘
```
- Evidence links open the source URL (from KB card) in a new tab with `rel="noopener noreferrer"`; hover shows quote.
- **INSUFFICIENT EVIDENCE** variant (U10): big "Not enough evidence, we don't guess" + **What was missing** list
  (e.g. "Found 2 of 3 required independent sources", "Only 1 of 2 platforms (hackernews)", "Risk scorer abstained: no
  evidence", "Confidence 0.42 below 0.50") + suggestion "Approve a deep search" / "Add details". No GO/NO-GO shown.
- Gate-failure NO-GO variant: names the failed gate + reason (hours gap / blocker / product named with link).
- Challenge flow (U8): click → spinner → toast + inline result "Defended: quote …" or "Removed: evidence did not support
  the claim → new version v3 (score 68.4, PIVOT)".
- What-if flow (U7): Apply → live panel → v(n+1) card shown side by side with v(n), deltas highlighted.

## 6. States & errors
Loading skeletons; empty states (no ideas yet); error banner with request id; 429 → "Slow down, try in N s"; budget
stopped → partial results + explanation.

## 7. Visual design
shadcn/ui neutral theme; verdict colours: GO green, PIVOT amber, NO-GO red, INSUFFICIENT_EVIDENCE slate; always with text
labels and icons. Responsive (laptop first). Dark mode optional.
