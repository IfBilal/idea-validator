#!/usr/bin/env bash
# Fresh-clone check (P11-T06). Clones the repo into a new temp directory and runs every documented step that needs
# no credentials and no cluster. Fails loudly on the first broken step. Optional tools that are absent are reported
# as SKIPPED (never as passed). See docs/FRESH_CLONE_TEST.md.
#
#   scripts/fresh_clone_check.sh [SOURCE] [REF]
#     SOURCE  repository to clone (default: this checkout, or $FRESH_CLONE_SOURCE; use the GitHub URL to test the real thing)
#     REF     branch or commit to check out (default: the branch currently checked out in SOURCE, else main)
#   KEEP=1 keeps the temp directory afterwards.
set -Eeuo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE="${1:-${FRESH_CLONE_SOURCE:-$HERE}}"
REF="${2:-$(git -C "$HERE" rev-parse --abbrev-ref HEAD 2>/dev/null || echo main)}"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/idea-validator-fresh.XXXXXX")"
PASSED=() SKIPPED=()

cleanup() { [ "${KEEP:-0}" = 1 ] && echo "kept: $WORK" || rm -rf "$WORK"; }
trap cleanup EXIT
trap 'echo "FAILED at line $LINENO: $BASH_COMMAND (clone: $WORK)" >&2' ERR

step() { echo; echo "=== $1"; }
ok() { PASSED+=("$1"); }
skip() { echo "SKIPPED: $1"; SKIPPED+=("$1"); }
have() { command -v "$1" >/dev/null 2>&1; }

have git || { echo "git is required" >&2; exit 2; }
have uv || { echo "uv is required (https://docs.astral.sh/uv/)" >&2; exit 2; }

step "clone $SOURCE ($REF) into $WORK"
git clone --quiet "$SOURCE" "$WORK/repo"
cd "$WORK/repo"
git checkout --quiet "$REF" 2>/dev/null || git checkout --quiet "origin/$REF"
echo "HEAD $(git rev-parse --short HEAD)"
[ ! -e .env ] || { echo ".env must not be in a fresh clone" >&2; exit 1; }
ok clone

step "uv sync --all-packages --frozen"
uv sync --all-packages --frozen
ok sync

step "lint (ruff, format, import layering)"
uv run ruff check .
uv run ruff format --check .
uv run lint-imports
ok lint

step "typecheck (mypy)"
uv run mypy
ok typecheck

step "unit tests (not integration)"
uv run pytest -q -m "not integration"
ok unit-tests

step "verdict engine tests (golden vectors)"
uv run pytest services/verdict -q
ok verdict-tests

step "schema drift"
uv run pytest libs/common/tests/test_schema_drift.py -q
ok schema-drift

step "generated Kubernetes manifests match the generator"
uv run --no-project --with pyyaml python scripts/gen_k8s.py --check
ok gen-k8s-check

step "make dev-env creates a git-ignored .env without credentials from outside"
make dev-env
git check-ignore -q .env || { echo ".env is not git-ignored" >&2; exit 1; }
[ -z "$(git status --porcelain)" ] || { echo "working tree dirty after dev-env" >&2; git status --short; exit 1; }
ok dev-env

step "docker compose config"
if have docker && docker compose version >/dev/null 2>&1; then
  set -a; . ./.env; set +a
  docker compose config -q
  ok compose-config
elif have podman && podman compose version >/dev/null 2>&1; then
  set -a; . ./.env; set +a
  podman compose config >/dev/null
  ok compose-config
else
  skip "compose-config (no docker compose or podman compose)"
fi

step "kustomize build + kubeconform + kube-linter"
BUILD=""
if have kubectl; then BUILD="kubectl kustomize --load-restrictor=LoadRestrictionsNone"
elif have kustomize; then BUILD="kustomize build --load-restrictor LoadRestrictionsNone"; fi
if [ -n "$BUILD" ]; then
  $BUILD k8s/overlays/dev > "$WORK/all.yaml"
  [ -s "$WORK/all.yaml" ] || { echo "empty kustomize output" >&2; exit 1; }
  ok kustomize-build
  if have kubeconform; then kubeconform -strict -summary "$WORK/all.yaml"; ok kubeconform
  else skip "kubeconform (not installed)"; fi
  if have kube-linter; then kube-linter lint "$WORK/all.yaml"; ok kube-linter
  else skip "kube-linter (not installed)"; fi
else
  skip "kustomize build, kubeconform, kube-linter (no kubectl or kustomize)"
fi

echo
echo "PASSED : ${PASSED[*]}"
echo "SKIPPED: ${SKIPPED[*]:-none}"
echo "FRESH CLONE CHECK OK"
