## What & why
<!-- one purpose per PR -->
Closes #
Requirement IDs (docs/harness/02_REQUIREMENTS.md): 
Phase / task: P?-T??

## How it was tested
- [ ] Unit / component tests added or updated
- [ ] `make lint typecheck test` green locally
- [ ] Module-specific checks (verdict vectors / retrieval gate / behaviour tests) if relevant

## Security checklist (13_SECURITY.md §12)
- [ ] No secrets/keys/tokens in code, config, tests, fixtures, logs, or images
- [ ] No new outbound network path except via M5; no new egress NetworkPolicy rule
- [ ] All new inputs validated (Pydantic, limits) and outputs schema-validated
- [ ] Fetched/user text only inside data delimiters in prompts
- [ ] No model output can influence the verdict except via evidence-backed dimension scores
- [ ] New endpoints authenticated / user-scoped / internal-token protected
- [ ] Containers non-root, pinned, scanned; no `latest`
- [ ] Dependencies pinned; audits clean

## Versions
- [ ] If prompts/rubrics/weights/thresholds changed: version bumped + evaluation result attached

## AI usage
- [ ] Row added to docs/ai-usage-log.md (what AI wrote, what we changed, why)

## Screenshots (UI / infra changes)
