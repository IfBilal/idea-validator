# AI-usage log (Description §1.3, Guide §11.2)
Every PR with AI-written code adds a row. Be specific: humans are responsible for every line.

| Date | Tool | What you asked for | What you changed | Why |
|---|---|---|---|---|
| 2026-11-26 | Claude | Generate a project harness (specs, plan, gates, seeds) from the two PDFs | _to fill: what the team edited after review_ | _to fill_ |
| 2026-11-26 | Claude Code | Rebalance Dev A / Dev B ownership across every doc (UI to Dev A; M7, M12, Skeptic to Dev B), fill in names, GitHub usernames and roll numbers | Chose the split and the names; reviewed the diff of 15 files | Docs only; humans must confirm the schedule in `18` still fits their hours |
| 2026-11-26 | Claude Code | Set the GitHub repo description and topics; move `ask` permission rules to `allow` in `.claude/settings.json` (PR #2) | Decided to loosen prompts; kept the deny list | Config only; reviewer must check the deny list is intact |
| 2026-11-26 | Claude Code | Update README (prerequisites, clone step, `docs/source` description) and this log (P0-T09, P0-T10) | _to fill_ | Docs only |
