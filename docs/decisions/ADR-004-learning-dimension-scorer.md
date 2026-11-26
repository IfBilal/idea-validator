# ADR-004: Learning & portfolio value is scored by the "Feasibility for this student" scorer
- **Status:** Accepted (pending instructor confirmation, see 27 §B)
- **Context:** M9 specifies four scorers; §5.1 defines six dimensions. Competition & Differentiation share a scorer; Learning has no named scorer.
- **Options:** (1) a fifth scorer; (2) compute learning purely from features; (3) Feasibility-for-this-student scorer outputs feasibility + learning.
- **Decision:** (3) with its own rubric (`learning@v1`) and feature prior (skill_novelty, cv_signal).
- **Why:** both dimensions depend on the same student profile (skills, interests, hours); keeps the four-scorer architecture of Figure 1.
- **Cost:** the scorer makes two rubric calls; slightly longer latency.
- **AI use:** Drafted with AI; reviewed by both members.
