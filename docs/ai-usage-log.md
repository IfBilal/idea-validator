# AI-usage log (Description §1.3, Guide §11.2)
Every PR with AI-written code adds a row. Be specific: humans are responsible for every line.

| Date | Tool | What you asked for | What you changed | Why |
|---|---|---|---|---|
| 2026-11-26 | Claude | Generate a project harness (specs, plan, gates, seeds) from the two PDFs | _to fill: what the team edited after review_ | _to fill_ |
| 2026-11-26 | Claude Code | Rebalance Dev A / Dev B ownership across every doc (UI to Dev A; M7, M12, Skeptic to Dev B), fill in names, GitHub usernames and roll numbers | Chose the split and the names; reviewed the diff of 15 files | Docs only; humans must confirm the schedule in `18` still fits their hours |
| 2026-11-26 | Claude Code | Set the GitHub repo description and topics; move `ask` permission rules to `allow` in `.claude/settings.json` (PR #2) | Decided to loosen prompts; kept the deny list | Config only; reviewer must check the deny list is intact |
| 2026-11-26 | Claude Code | Update README (prerequisites, clone step, `docs/source` description) and this log (P0-T09, P0-T10) | _to fill_ | Docs only |
| 2026-11-26 | Claude Code | P0-T03..T05, P1-T01, P1-T02: uv workspace, shared schemas and libs, app factory, service template, 13 stub services | _to fill_ | Dev A must review P0-T03; humans must read libs/common before relying on it |
| 2026-11-26 | Claude Code | P2-T01..T05: M11 Verdict Engine ported from `eval/verdict/reference_engine.py`, endpoints, tests | Reviewed by the verdict-guardian agent twice; findings fixed | Verdict logic: both members must be able to explain every rule |
| 2026-11-26 | Claude Code | P2-T06, P2-T07 (part), P2-T09 (part): M2 tables, Alembic migration, argon2id auth with lockout, versioned profiles | _to fill_ | Security-relevant (passwords, lockout): reviewer should read `services/auth.py` |
| 2026-11-27 | Claude Code | P2-T07 (rest) and P2-T08: M2 storage for ideas, versions, verdicts, runs, approvals, compare, and the Redis verdict cache with version-aware keys | _to fill_ | Reviewer: check ownership (another user's idea is a 404) and that every cache key has a TTL |
