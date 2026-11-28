#!/usr/bin/env bash
# P11-T01: measure what the pods really use under load and print requests/limits to put in scripts/gen_k8s.py.
# Needs the deployed stack and metrics-server. Usage: scripts/measure_resources.sh [SECONDS=90] > eval/results/resources.json
set -euo pipefail
cd "$(dirname "$0")/.."
source "$(dirname "$0")/lib/kube_guard.sh"
export KUBECONFIG="$PROJECT_KUBECONFIG"
use_project_cluster || exit 1

NS=idea-validator
SECONDS_TO_RUN="${1:-90}"
[[ "$SECONDS_TO_RUN" =~ ^[0-9]{1,4}$ ]] || { echo "seconds must be a number" >&2; exit 2; }
samples="$(mktemp)"
trap 'rm -f "$samples"; kill "${load_pid:-0}" 2>/dev/null || true' EXIT

./scripts/load_test.sh "$SECONDS_TO_RUN" >&2 &
load_pid=$!
end=$((SECONDS + SECONDS_TO_RUN))
while [ "$SECONDS" -lt "$end" ]; do
  kubectl -n "$NS" top pod --no-headers 2>/dev/null | awk '{print $1, $2, $3}' >>"$samples" || true
  sleep 5
done
wait "$load_pid" 2>/dev/null || true
[ -s "$samples" ] || { echo "no samples: is metrics-server running?" >&2; exit 1; }
python3 scripts/measure_resources.py <"$samples"
