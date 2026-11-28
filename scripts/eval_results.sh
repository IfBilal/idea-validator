#!/usr/bin/env bash
# `make eval-results`: copy the newest evaluation Job's result out of the cluster.
# The Job writes to an emptyDir and prints the result document as its LAST log line, so there is no
# volume to copy from: this saves that line as eval/results/<UTC timestamp>.json and prints the path.
# It never writes baseline.json: a baseline comes only from a human-reviewed run (--write-baseline).
set -euo pipefail
cd "$(dirname "$0")/.."
source "$(dirname "$0")/lib/kube_guard.sh"
use_project_cluster || exit 1

NS=idea-validator
out="${EVAL_RESULTS_DIR:-eval/results}"
mkdir -p "$out"
line="$(kubectl -n "$NS" logs job/eval --tail=1)" || {
  echo "no logs for job/eval: run 'make eval' first" >&2
  exit 1
}
case "$line" in
  '{"schema":"eval-results@v1"'*) ;;
  *)
    echo "the last log line of job/eval is not a result document (the job may have stopped early):" >&2
    echo "  kubectl -n $NS logs job/eval" >&2
    exit 1
    ;;
esac
file="$out/$(date -u +%Y%m%dT%H%M%SZ).json"
printf '%s\n' "$line" >"$file"
echo "$file"
