# ADR-001: The verdict is computed by deterministic code (M11), never by a model
- **Status:** Accepted
- **Context:** Description §5: "The language model never outputs the verdict." LLM judges drift unless rubrics are locked and evidence required; weighted totals flip with small changes.
- **Options:** (1) LLM outputs verdict directly; (2) LLM scores + LLM final judgement; (3) LLM evidence-backed scores + deterministic engine with gates, thresholds, confidence, sensitivity.
- **Decision:** (3). M11 is plain Python with rules in a ConfigMap, no LLM/network/randomness/clock.
- **Why:** explainable, repeatable (same input → same output), unit-testable (24 golden vectors incl. the PDF worked example), tunable against human labels.
- **Cost:** need good scores + features; rules must be tuned (Phase 10).
- **AI use:** Drafted with AI from the project description; reviewed by both members.
