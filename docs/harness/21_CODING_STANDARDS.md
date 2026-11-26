# 21 — Coding Standards & Conventions

## 1. General
- Small, single-purpose modules and PRs (< 400 changed lines excluding generated/fixtures).
- Readable over clever: both members must be able to explain every file.
- Type everything (Python type hints, TS strict). No `Any` in public interfaces.
- No dead code, no commented-out code, no TODO without an issue number (`# TODO(#42): ...`).

## 2. Python
- Layering: `api/routes.py` → `services/` → `repositories/` / `clients/`. Enforced with **import-linter** contracts
  (routes cannot import repositories; services cannot import fastapi).
- Pydantic v2 models with `model_config = ConfigDict(extra="forbid", frozen=True)` for messages.
- Async everywhere for I/O (`httpx.AsyncClient`, SQLAlchemy async). Never block the event loop (CPU work → `anyio.to_thread`).
- Settings via `pydantic-settings` (`env_prefix` per service). No hard-coded URLs/hosts/ports/secrets; no `localhost`
  defaults in non-test settings.
- Errors: domain exceptions (`NotFound`, `BudgetExceeded`, `UpstreamTimeout`, …) mapped to the standard error JSON in one
  exception handler. Never leak stack traces to clients.
- Logging: `structlog` JSON, include `request_id`, `run_id`, `service`. No `print`. Redaction processor on.
- Naming: snake_case modules/functions, PascalCase classes, UPPER_CASE constants; dimension names exactly
  `demand_pain, competition, differentiation, feasibility, risk, learning`; verdict strings exactly
  `GO, PIVOT, NO-GO, INSUFFICIENT_EVIDENCE`.
- Determinism: M11 + routing functions + features are pure; seed every random process (clustering, splits) with 42.
- Tests mirror source tree; names `test_<unit>_<behaviour>()`.

## 3. TypeScript / React
- Function components + hooks; server state via TanStack Query; no global mutable state beyond auth context.
- API layer `src/api/` typed from schemas (zod); components never call `fetch` directly.
- Accessibility: semantic elements, labels on inputs, verdict badge has text (not colour only), keyboard reachable dialogs (Radix).
- Test with Testing Library queries by role/label; MSW for network.

## 4. Git & PR
- Branch: `<type>/<module>-<short-desc>`. Conventional commit messages (`feat(verdict): add duplicate gate`).
- PR template fully filled: linked issue, requirement IDs, tests added, security checklist, screenshots for UI, AI usage.
- Reviewer = other member (CODEOWNERS); agent-generated PRs still need human approval.
- Squash merge with conventional title; delete branch after merge.

## 5. Config & versions
- All tunables in `config/*.yaml` → ConfigMaps. Changing weights/thresholds/rubrics/prompts = version bump + eval run.
- Pin every dependency (uv.lock, package-lock.json), every image (tag + digest), every Action (`@v4` minimum; SHA preferred).

## 6. Documentation
- Each service has `README.md`: purpose, module id, endpoints, config vars, how to run/test, owner.
- Decisions → ADR (`docs/decisions/ADR-NNN-title.md`, ≤ 1 page, template in folder).
- Keep harness docs true: if behaviour diverges, update the doc in the same PR.
