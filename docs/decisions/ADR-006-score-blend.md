# ADR-006: Dimension score = blend of computed-feature prior and model judgment
- **Status:** Proposed (tune α in Phase 10)
- **Context:** Description §5.1: each score combines computed features with the fine-tuned model's judgment under a versioned rubric.
- **Options:** (1) model only with features in prompt; (2) features only; (3) α·model + (1−α)·feature prior, rounded to 0.5.
- **Decision:** (3), α = 0.6 in `config/verdict.yaml`.
- **Why:** model adds judgement on text; prior anchors scores to measurable evidence and reduces drift.
- **Cost:** one more parameter to tune and justify.
- **AI use:** Drafted with AI; reviewed by both members.
