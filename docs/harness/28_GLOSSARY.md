# 28 — Glossary

| Term | Meaning |
|---|---|
| Idea card | Normalised record of a product/complaint/request from a public source (Guide §2.1), id `card_xxxxxxxx` |
| Chunk / evidence id | Piece of a card's text indexed in the vector store; id `card_8f21#c2` used for citations |
| Evidence item | Retrieved chunk returned by M6 with quote, source, URL (Guide §2.2) |
| Dimension | One of six scored aspects: demand_pain, competition, differentiation, feasibility, risk, learning (0–5, higher better for student) |
| Rubric | Versioned file per dimension with anchors for every level 0–5, features, schema, examples |
| Feature | Number computed in code from the KB (n_sources, trend_growth, …) |
| Abstain | Scorer returns no score because it has no valid evidence |
| Gate | Hard check before scoring: Evidence, Feasibility, Risk, Duplicate |
| T_go / T_no / floor | Score thresholds for GO and NO-GO; minimum per-dimension score (first five dims) |
| Weighted score | Σ weight × score / 5 (0–100) |
| Confidence | 0.7 × mean dimension confidence + 0.3 × min(1, sources/5) |
| Sensitivity / BORDERLINE | Re-running the verdict with perturbed weights/scores; BORDERLINE if any run flips it |
| What-if | Smallest change that changes the verdict; applying it creates a new idea version |
| Challenge | Skeptic objections checked by the critic; accepted ones lower scores; user can challenge a claim |
| Debate | Ideator → Gap Analyst → Skeptic → Critic loop in Discover (≤ 3 rounds) |
| Niche | Cluster of cards with a name, size, monthly series and status (rising/flat/saturated) |
| Gap | Recurring complaint/request with no product addressing it; feeds idea generation and scoring |
| Run | One Discover/Validate/what-if/challenge execution with a `run_id`, trace and budget |
| Trace record | One step's record (agent, kind, tool, tokens, cost, latency, …) (Guide §2.3) |
| Replay | Re-running a stored run with recorded tool results |
| Drift report | After ingestion: freshness, niche coverage, verdict distribution shift |
| Budget | Per-run caps on tokens, pages, steps, cost; over budget → stop or ask user |
| Spotlighting | Wrapping untrusted text in delimiters and telling the model it is data |
| Sanitiser | M5 component that strips HTML and instruction-like text from fetched pages |
| Input rail | M12 check on user text before it reaches any model |
| HITL / interrupt | LangGraph pause waiting for the user's answer/approval |
| Labelled set | Human-verdict ideas used only for tuning/measuring M11, never for training |
| Hand-checked set | ≥ 60 fine-tune examples whose labels humans verified; the fine-tune test set |
| False-GO | System says GO where humans said NO-GO; the worst error, reported separately |
| ADR | Architecture/design decision record (Guide §11.1) |
