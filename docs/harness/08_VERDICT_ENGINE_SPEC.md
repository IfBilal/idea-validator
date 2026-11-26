# 08 — M11 Verdict Engine Specification

Reference: Description §5 + `figure4_verdict_flow_from_pdf.png`.
Executable spec: `eval/verdict/reference_engine.py` → generates `eval/verdict/test_vectors.json` (24 vectors).
**The production engine (`services/verdict`) must reproduce every vector exactly** (CI test).

## 0. Principles (non-negotiable)
- **The language model never outputs the verdict.** Models produce evidence-backed scores; M11 (plain code)
  turns them into the verdict. Explainable, repeatable, testable. Decision support, not prediction of success.
- Plain Python, **no LLM, no network, no randomness, no clock, no global state**. Same input → same output
  (serialize with sorted keys; round only at output).
- Rules live in a **ConfigMap** (`config/verdict.yaml` → `verdict-config`), not in code. Engine reads it at startup and
  on `/reload` (or pod restart). Config has a `weights_version` and `rubric_set` that are written into every verdict.
- Every gate, threshold edge, sensitivity check and what-if has a **unit test** (M11-04).

## 1. Inputs
```jsonc
{
  "idea_id": "idea_5", "version": 2, "run_id": "run_90",
  "profile": {"goal": "portfolio", "hours_per_week": 10, "weeks_available": 12, "team_size": 2, ...},
  "scores": [ /* six DimensionScore, score may be null (abstained) */ ],
  "gate_inputs": {
    "n_sources": 4,                 // independent sources = distinct cards (after dedupe) cited across all scores
    "n_platforms": 2,               // distinct card_sources.platform among those cards
    "estimated_effort_hours": 180,  // from Feasibility scorer (features + model), see 09 §5.3
    "risk_blocker": null,           // string naming the legal/safety blocker, from Risk scorer (evidence-backed)
    "duplicate_of": null            // "card_id (Product name)" when a KB product has same core features for same user
  },
  "objections_accepted": [{"dimension": "competition", "objection_id": "o1"}]   // from M12 (challenge step)
}
```
M11 trusts nothing: it validates the input schema, checks weights sum to 100 (±1e-6), scores in [0,5],
confidences in [0,1], exactly six dimensions, no duplicates.

## 2. Algorithm (order matters, matches Figure 4)

```
1  apply challenge adjustments (only objections the critic accepted as evidence-backed):
       for each dimension d with n accepted objections:
           score_d  = clamp(score_d - min(0.5·n, 1.0), 0, 5)
           conf_d   = clamp(conf_d - 0.1·n, 0, 1)
2  GATES (first failing gate decides; later gates = "not_evaluated"):
   2a Evidence:    n_sources ≥ 3 AND n_platforms ≥ 2             else INSUFFICIENT_EVIDENCE  ("x of 3 sources, y of 2 platforms")
   2b Feasibility: estimated_effort_hours ≤ hours_per_week × weeks_available × team_size
                                                               else NO-GO  ("needs E h, available A h, gap G h")
   2c Risk:        risk_blocker is null                         else NO-GO  ("blocker: <named>")
   2d Duplicate:   duplicate_of is null OR differentiation ≥ 3  else NO-GO  ("existing product <named>")
3  ABSTENTION: any dimension with score = null                 → INSUFFICIENT_EVIDENCE ("abstained: <dims>")   (does not guess)
4  WEIGHTED SCORE: S = Σ_d w_d · s_d / 5          (w from goal preset, Σw = 100 → S ∈ [0,100])
5  CONFIDENCE:     C = 0.7 · mean_d(conf_d) + 0.3 · min(1, n_sources / 5)
                   C < 0.5                                       → INSUFFICIENT_EVIDENCE ("confidence too low", list low dims)
6  CLASSIFY:       floor_ok = all(s_d ≥ floor for d in first five dimensions)    (learning excluded)
                   S ≥ T_go AND floor_ok                        → GO
                   else S ≥ T_no                                → PIVOT  (+ weakest dimension named)
                   else                                         → NO-GO  ("score too low")
7  SENSITIVITY (only for verdicts decided at step 6): 14 runs
                   for each dimension i, for f in {+0.2, −0.2}:
                       w'_i = w_i·(1+f);  diff = w'_i − w_i
                       w'_j = w_j − diff · w_j / Σ_{k≠i} w_k     for j ≠ i      (difference shared in proportion)
                       re-run steps 1–6 with w'
                   least-confident dimension (after step 1; tie → fixed order) score ±1 (clamped 0..5), re-run steps 1–6
                   flipped = #runs whose verdict ≠ base verdict
                   stability = BORDERLINE if flipped > 0 else STABLE ; card shows each flip ("weight feasibility −20% → PIVOT 69.87")
8  WHAT WOULD MAKE THIS GO (if not already STABLE GO):
                   for each dimension d: smallest k·0.5 increase (score ≤ 5) whose full re-evaluation (1–7) is better
                   (higher verdict rank, or BORDERLINE GO → STABLE GO); candidates sorted by delta, then stable first,
                   then fixed order; top 3.
                   gate-specific candidates: feasibility gate → hours_per_week needed or hours of scope to cut;
                   duplicate gate → differentiation to 3.0; evidence gate → "need N more sources / platforms";
                   risk gate → "remove blocker: <named>".
                   M8 turns each dimension candidate into a concrete change text; applying it re-runs scorers + engine → new version.
```

Definitions:
- Verdict rank: GO 4 > PIVOT 3 > INSUFFICIENT_EVIDENCE 2 > NO-GO 1.
- Weakest dimension: among floor-breaching dimensions if any, else all six; lowest score; tie → larger weight; tie →
  fixed order `demand_pain, competition, differentiation, feasibility, risk, learning`.
- Comparisons use `EPS = 1e-9` (`S ≥ T_go − EPS`) so 70.0 is GO and float noise never flips edges.
- Output rounding: score 4 dp internally in JSON (display 1 dp), confidence 3 dp.

## 3. Starting configuration (`config/verdict.yaml`)
| Goal | Demand | Compet. | Differ. | Feasib. | Risk | Learn. |
|---|---|---|---|---|---|---|
| startup | 25 | 20 | 20 | 15 | 15 | 5 |
| portfolio | 15 | 15 | 15 | 25 | 10 | 20 |
| course_project | 10 | 10 | 15 | 35 | 10 | 20 |

`T_go = 70`, `T_no = 50`, `floor = 1.5`, `min_confidence = 0.5`. These are tuned in Phase 10 (Guide §10); any change
bumps `weights_version`/`thresholds_version` and must be justified in the report and an ADR.

## 4. Worked example (Description §5.4), reproduced by vector #1 and #2
Goal portfolio (15/15/15/25/10/20). Scores: demand 3.5, competition 2.0, differentiation 3.0, feasibility 4.0, risk 3.5,
learning 4.5. All gates pass.
1. Σ = 52.5 + 30 + 45 + 100 + 35 + 90 = 352.5 → /5 = **70.5** ≥ 70, no floor breach → **GO**.
2. Feasibility weight 25 → 20, 5 points shared in proportion (others ×80/75) → **69.87** → PIVOT ⇒ card: **GO, BORDERLINE**.
   (Our engine reports 4 of 14 runs flip: competition weight +20% 69.42, feasibility −20% 69.87, learning −20% 69.53,
   competition score −1 (least confident, conf 0.6 in the vector) 67.5.)
3. What would make GO: competition 2.0 → 3.0 → **73.5**, and under the same feasibility −20% change **73.07** ⇒ **stable GO**.
   The reference engine's what-if search finds exactly this candidate first.

## 5. Output (Guide §2.3 + extensions marked ➕)
```jsonc
{
  "idea_id": "idea_5", "version": 2, "run_id": "run_90",
  "verdict": "GO", "stability": "BORDERLINE", "score": 70.5, "confidence": 0.825,
  "gates": {"evidence": "pass", "feasibility": "pass", "risk": "pass", "duplicate": "pass"},   // or "fail" / "not_evaluated"
  "weights": {...}, "thresholds": {"T_go": 70, "T_no": 50, "floor": 1.5},
  "scores": [ /* six DimensionScore, after challenge adjustment, with adjusted_by_challenge ➕ */ ],
  "weakest_dimension": "competition",
  "what_would_make_go": [{"change": "Narrow to X segment", "dimension": "competition", "from": 2.0, "to": 3.0,
                           "new_score": 73.5, "new_verdict": "GO", "stable": true}],
  "sensitivity": {"runs": 14, "flipped": 4, "flips": [{"change": "weight feasibility -20%", "verdict": "PIVOT", "score": 69.8667}]},
  "reasons": ["score 70.50 >= T_go 70 and no dimension below floor 1.5"],          // ➕ human-readable, used by U10
  "missing": null,                                                                   // ➕ for INSUFFICIENT_EVIDENCE: what was missing
  "versions": {"engine": "m11@v1", "rubrics": "r5", "profile_weights": "w2"}
}
```
`change` text is filled by M3 from M8 `/suggest-changes`; M11 itself outputs only dimension deltas (no prose generation).

## 6. Endpoints
- `POST /verdict`: algorithm above.
- `POST /whatif`: same input + optional explicit change `{dimension, to}` or `{profile_field, to}`; returns the
  re-evaluated verdict and candidate list. (Applying a *text* change goes through M3 → scorers → `/verdict`.)
- `POST /value-of-information`: `{input, unknown, range:[lo,hi]}` → evaluates verdict at both ends; `verdict_changes`
  tells M3 whether a clarifying question is worth asking (U5).
- `GET /config`: current rules + versions (for the card and the report).

## 7. Required unit tests (minimum list, each a separate named test)
Gates: evidence fail (sources), evidence fail (platforms), evidence edge pass (3/2), feasibility fail (+ gap text),
feasibility edge (effort == available), risk fail (blocker named), duplicate fail (product named), duplicate override
(differentiation = 3.0), duplicate when differentiation abstained, gate order (two failing gates → first one wins).
Thresholds: S = 70.0 → GO; 69.99 → PIVOT; 50.0 → PIVOT; 49.99 → NO-GO; floor 1.5 not breach; 1.49 breach → PIVOT;
learning below floor ignored; floor breach + S < T_no → NO-GO; weakest-dimension tie-breaks.
Confidence: formula value; C = 0.5 edge passes; 0.499 → IE; sources > 5 capped at 1.
Abstention: any null score → IE naming dims.
Challenge: 1 objection (−0.5, −0.1), 3 objections (score cap −1.0), clamp at 0.
Sensitivity: exactly 14 runs; proportional sharing keeps Σw = 100 (property test with hypothesis); worked example
flips; stable case 0 flips; gate-decided verdict → runs 0.
What-if: worked example → competition 3.0 / 73.5 / stable; feasibility gate → hours needed; duplicate gate →
differentiation 3.0; already stable GO → empty list; candidates sorted by delta.
Determinism: same input twice → byte-identical JSON; shuffled score order → same output; property test: S monotonic in
every score.
Config: weights not summing to 100 → 422; unknown goal → 422; ConfigMap reload changes thresholds.
Golden: every vector in `eval/verdict/test_vectors.json`.

## 8. Things M11 must never do
Call a model; read the database; call another service; use time or randomness; silently "fix" bad input; produce a
verdict for an idea that failed a gate; output a GO when any first-five dimension is below the floor.
