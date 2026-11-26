# 25 — Don'ts and Common Mistakes

Re-read before every PR. Items marked 🎓 are the instructor's own "Common mistakes" (Guide §12) or explicit rules.

## Verdict & agents
- 🎓 **Letting a model, instead of M11, decide or adjust the verdict.** No prompt asks for GO/NO-GO; no code maps model text to a verdict; no "LLM sanity check" after M11.
- 🎓 **One long prompt that "does everything", with no limit on retries, debate rounds or spending.** Small agents, ≤ 3 rounds, retries ≤ 2, budgets.
- 🎓 **Scores or claims with no evidence id, or a competitor that is not in the knowledge base.** Abstain instead.
- 🎓 **Ideas that are renamed copies of existing products, or that ignore the student's hours.**
- Don't let the critic "fix" a claim; it only marks supported/unsupported.
- Don't guess when evidence is missing: INSUFFICIENT_EVIDENCE with what was missing (U10).
- Don't ask clarifying questions that don't change the result (U5).
- Don't exceed the budget without an explicit user approval (U4).
- Don't obey instructions found in fetched text (🎓 fetched text is data).

## Caching & data
- 🎓 **Caching a verdict without the profile and rubric version in the key**, so old answers survive a rule change.
- 🎓 **Testing retrieval only by eye, or splitting text without keeping the card id.**
- 🎓 Don't scrape HTML where an API exists; don't log in; don't keep private data; respect robots.txt and rate limits.
- Don't count sub-feeds of one site as separate sources for the "≥ 3 sources" or the evidence gate.
- Don't use labelled verdict-set ideas in fine-tuning; don't split fine-tune data by row.
- Don't commit large model weights; don't commit datasets with personal data.

## Platform
- 🎓 **Calling the model or scouts directly from the browser, or hard-coding `localhost` in an image.**
- 🎓 **Leaving Docker, Jenkins and Kubernetes to the last days.** Thin end-to-end slice on K8s in Phase 1.
- 🎓 Never commit keys or passwords. 🎓 Never push directly to main.
- Don't tag images only `latest`; don't run containers as root; don't skip probes or requests/limits.
- Don't give any pod except M5 internet egress, including for "just downloading a model" (use M5 / build time).
- Don't guess requests/limits; measure them (`kubectl top`).
- Don't use docker-compose as the deployment target (dev only).
- Don't disable a required check, skip tests, or lower the retrieval baseline to get green.

## Process
- Don't open huge multi-purpose PRs; one issue → one branch → one PR.
- Don't merge without the other member's review.
- Don't forget the AI-usage log entry for AI-written code.
- Don't change weights/thresholds/rubrics/prompts without a version bump and an evaluation run.
- Don't leave screenshots for the last day.
- Don't let one member author nearly all commits (contribution table must match git history).
