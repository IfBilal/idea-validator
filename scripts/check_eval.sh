#!/usr/bin/env bash
# Nightly evaluation gate: compare the newest eval/results/*.json with eval/results/baseline.json.
# Fails when agreement drops > 5 points, false-GO increases, or any behaviour check is below 100%.
set -euo pipefail
cd "$(dirname "$0")/.."
source "$(dirname "$0")/lib/kube_guard.sh"
use_project_cluster || exit 1   # the gate belongs to the project cluster's evaluation, never another one
baseline="${BASELINE:-eval/results/baseline.json}"
current="${1:-$(ls -1t eval/results/*.json | grep -v baseline.json | head -n1)}"
[ -f "$baseline" ] && [ -f "$current" ] || { echo "missing $baseline or results file" >&2; exit 2; }
exec uv run python -m evaluation.cli gate "$baseline" "$current"
