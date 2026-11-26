# 15 — Git Workflow, GitHub Actions, Jenkins

Reference: `guide_fig2_cicd_from_pdf.png`. **GitHub Actions protects `main`. Jenkins delivers merged code and runs the
nightly evaluation.** Fail = pipeline stops, nothing is deployed. Rollback: `kubectl rollout undo`.

## 1. Git workflow (Guide §4.2) (humans do the settings, agents follow the flow)
- **Day 1**: create repo; add both members; **protect `main`**: no direct pushes, PRs only, required status checks (all
  jobs below), **1 approving review from the other member**, dismiss stale approvals, require branch up to date,
  include administrators. Add `CODEOWNERS` (`* @IfBilal @T361`).
- **One issue per task** (`20_TASK_BACKLOG.md` → GitHub issues via `scripts/create_issues.sh` using `gh issue create`),
  linked from the PR (`Closes #12`). Labels: module (`m1`…`m14`), type, phase.
- **Small branches with one purpose**: `feat/verdict-duplicate-gate`, `fix/kb-dedupe-by-url`, `test/scouts-source-failure`.
- **Conventional commits**: `feat(verdict): add duplicate gate`, `fix(kb): dedupe by url`, `test(scouts): add source failure case`,
  also `docs`, `chore`, `ci`, `refactor`, `perf`, `build`. Enforced by commitlint in CI (PR title + commits).
- **Deliberate merge conflict** (required, screenshot): Phase 1 task P1-T12: Dev A (Taimur) on `chore/conflict-a` and Dev B (Bilal) on
  `chore/conflict-b` both edit the same lines of `config/verdict.yaml` comment header (or README section); merge A;
  B's PR shows conflict; resolve in the PR (GitHub UI or locally), reviewer approves; screenshot conflict + resolution.
- Both members must author PRs and review the other's (contribution table must match git history).

## 2. GitHub Actions: `.github/workflows/pr-checks.yml` (on `pull_request` to main)
Jobs (each a required check):
| Job | Steps |
|---|---|
| `lint-type` | uv sync → `ruff check .` → `ruff format --check .` → `mypy services libs jobs` → commitlint |
| `unit-tests` | `pytest -q -m "not integration"` with coverage gates; **includes `pytest services/verdict -q` and golden vectors** |
| `integration-tests` | testcontainers (Postgres/Redis/Qdrant) `pytest -m integration` |
| `ui-tests` | `npm ci` → `npm run lint` → `npm run typecheck` → `npm test -- --run` (component tests) → `npm run build` |
| `retrieval-gate` | `pytest eval/retrieval -q` (fails if below `baseline.json`) |
| `contracts` | schema drift check (Pydantic vs `schemas/*.json`), registry version check |
| `docker` | matrix over all module services: `docker buildx build -t <svc>:${{ github.sha }}` (gha cache) → **Trivy** image scan (`exit-code 1` on CRITICAL) → Syft SBOM artifact |
| `manifests` | `kustomize build k8s/overlays/dev` → **kubeconform** (strict, k8s version pinned) → **kube-linter** |
| `security` | gitleaks (full history), pip-audit, npm audit --audit-level=high, Trivy fs + config |
Collect screenshots: one **green** run and one **failing** run (e.g. intentionally break a verdict test on a throwaway PR).
Concurrency: cancel in-progress runs for the same PR. Permissions: `contents: read` only.

## 3. Jenkins (after merge to main, and nightly): `Jenkinsfile`
- Jenkins runs in Docker (`jenkins/jenkins:lts-jdk17` pinned) with Docker CLI + socket (or DinD) and kubectl; plugins:
  workflow-aggregator, git, docker-workflow, credentials-binding, timestamper, (optional) configuration-as-code.
- Credentials: `kubeconfig-kind` (Secret file), `registry-creds` (if registry has auth). **Never in the repo.**
- Trigger: **SCM polling `H/2 * * * *`** (GitHub webhook cannot reach a laptop; ngrok is the alternative) +
  **nightly cron `H 2 * * *`** for evaluation (`triggeredBy 'TimerTrigger'`).
- Stages: **Checkout → Build & tag (short SHA, all modules) → Push (registry) → Deploy to test cluster (kustomize edit
  set image + apply + `kubectl rollout status` with timeout) → Smoke test → [nightly] Full evaluation (eval Job) →
  Compare with baseline**. Any failure → pipeline stops, nothing promoted; on deploy/smoke failure → `kubectl rollout undo`
  for changed deployments (post-failure step).
- Keep screenshots: a **green** Jenkins run and a **failing** one (e.g. smoke test failing due to a deliberately broken
  readiness path on a test commit, then reverted).

## 4. Image tagging & promotion
Tags = `${GIT_COMMIT[0..6]}` (never only `latest`). Same SHA in all module images of a release. Kustomize overlay records
image tags (`kustomize edit set image`) so rollbacks are reproducible.

## 5. Rollout & rollback demonstration (screenshots)
1. Deploy release A (`abc1234`). 2. Deploy release B with a visible change; `kubectl rollout status deploy/gateway`.
3. Deploy a **bad** release C (e.g. readiness fails) → rollout stalls (maxUnavailable 0 keeps old pods serving).
4. `kubectl rollout undo deploy/gateway -n idea-validator` → back to B. `kubectl rollout history` screenshot.
