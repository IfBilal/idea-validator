#!/usr/bin/env bash
# `make eval-results`: copy the newest evaluation Job's result out of the cluster.
# The Job writes to an emptyDir and prints, as the last two log lines, the marker line
# `-----EVAL-RESULT-----` and the result document, so there is no volume to copy from: this saves
# that document as eval/results/<UTC timestamp>.json and prints the path.
# It refuses when the Job is still running, when the marker is missing, or when the last line is not
# a JSON document of schema eval-results@v1 with n_total > 0. It writes through a temp file and never
# replaces an existing file. It never writes baseline.json: a baseline comes only from a
# human-reviewed run (python -m evaluation.run --write-baseline).
set -euo pipefail
cd "$(dirname "$0")/.."
source "$(dirname "$0")/lib/kube_guard.sh"
use_project_cluster || exit 1

NS=idea-validator
MARKER='-----EVAL-RESULT-----'
out="${EVAL_RESULTS_DIR:-eval/results}"
mkdir -p "$out"

active="$(kubectl -n "$NS" get job eval -o jsonpath='{.status.active}' 2>/dev/null || true)"
if [ -n "$active" ] && [ "$active" != 0 ]; then
  echo "job/eval is still running: wait for it to finish" >&2
  exit 1
fi

logs="$(kubectl -n "$NS" logs job/eval --tail=2)" || {
  echo "no logs for job/eval: run 'make eval' first" >&2
  exit 1
}
marker_line="$(printf '%s\n' "$logs" | sed -n 1p)"
line="$(printf '%s\n' "$logs" | sed -n 2p)"
if [ "$marker_line" != "$MARKER" ]; then
  echo "the log does not end with the result marker (the job may have stopped early):" >&2
  echo "  kubectl -n $NS logs job/eval" >&2
  exit 1
fi
printf '%s' "$line" | python3 -c '
import json, sys
doc = json.loads(sys.stdin.read())
assert isinstance(doc, dict) and doc.get("schema") == "eval-results@v1", "wrong schema"
n_total = doc.get("n_total")
assert isinstance(n_total, int) and not isinstance(n_total, bool) and n_total > 0, "no items"
' 2>/dev/null || {
  echo "the last log line is not a valid eval-results@v1 document with n_total > 0" >&2
  exit 1
}

file="$out/$(date -u +%Y%m%dT%H%M%SZ).json"
[ ! -e "$file" ] || { echo "$file already exists: not overwritten" >&2; exit 1; }
tmp="$(mktemp "$out/.eval-result.XXXXXX")"
trap 'rm -f "$tmp"' EXIT
printf '%s\n' "$line" >"$tmp"
mv -n "$tmp" "$file"
[ -e "$file" ] && [ ! -e "$tmp" ] || { echo "could not write $file" >&2; exit 1; }
echo "$file"
