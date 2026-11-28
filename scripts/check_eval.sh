#!/usr/bin/env bash
# Nightly evaluation gate: compare the newest eval/results/*.json with eval/results/baseline.json.
# Fails when agreement drops > 5 points, false-GO increases, or any behaviour check is below 100%.
set -euo pipefail
cd "$(dirname "$0")/.."
source "$(dirname "$0")/lib/kube_guard.sh"
use_project_cluster || exit 1   # the gate belongs to the project cluster's evaluation, never another one
baseline="${BASELINE:-eval/results/baseline.json}"
current="${1:-$(ls -1t eval/results/[0-9]*.json 2>/dev/null | head -n1 || true)}"  # timestamped runs only
[ -f "$current" ] || { echo "no results file: run 'make eval' (needs eval/labelled/labelled_set.jsonl)" >&2; exit 2; }
[ -f "$baseline" ] || {
  echo "no $baseline. It is never created automatically: review a run by hand, then run" >&2
  echo "  python -m evaluation.run --write-baseline   (or copy the reviewed result to $baseline)" >&2
  exit 2
}
exec uv run python -m evaluation.cli gate "$baseline" "$current"
