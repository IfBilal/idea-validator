#!/usr/bin/env bash
# Jenkins nightly Eval stage and `make eval`: run the evaluation Job on the project cluster, save its
# result under eval/results/, then gate on the baseline (scripts/check_eval.sh).
# Goes through the kube guard (CI_KUBECONFIG, kind-idea-validator only) like every other cluster step.
#   TAG                  image tag of localhost:5001/evaluation (default: the commit SHA; never "latest")
#   EVAL_TIMEOUT         seconds to wait for the Job (default 7200)
# Exit: 0 gate passed; 1 Job or gate failed; 2 nothing to run (no labelled set, no manifest, no baseline).
set -euo pipefail
cd "$(dirname "$0")/.."
source "$(dirname "$0")/lib/kube_guard.sh"

NS=idea-validator
MANIFEST="${EVAL_MANIFEST:-k8s/jobs/eval.yaml}"
LABELLED="${EVAL_LABELLED:-eval/labelled/labelled_set.jsonl}"
TAG="${TAG:-$(git rev-parse --short=7 HEAD 2>/dev/null || echo dev)}"
TIMEOUT="${EVAL_TIMEOUT:-7200}"

# say what is missing before touching any cluster
[ -f "$LABELLED" ] || {
  echo "no $LABELLED: label the candidates first (eval/labelled/README.md). Nothing was run." >&2
  exit 2
}
[ -f "$MANIFEST" ] || { echo "missing evaluation job manifest $MANIFEST" >&2; exit 2; }
use_project_cluster || exit 1
if ! [[ "$TAG" =~ ^[A-Za-z0-9._-]{1,128}$ ]] || [ "$TAG" = latest ]; then
  echo "refusing image tag '$TAG': use a commit SHA, never 'latest'" >&2
  exit 1
fi

kubectl -n "$NS" delete job eval --ignore-not-found >/dev/null
sed -e "s#image: idea-validator/evaluation:unset#image: localhost:5001/evaluation:${TAG}#" "$MANIFEST" |
  kubectl apply -f - -n "$NS"

# wait for whichever ends first; the Job's own activeDeadlineSeconds bounds a hung run
kubectl -n "$NS" wait --for=condition=complete job/eval --timeout="${TIMEOUT}s" >/dev/null 2>&1 &
done_pid=$!
kubectl -n "$NS" wait --for=condition=failed job/eval --timeout="${TIMEOUT}s" >/dev/null 2>&1 &
failed_pid=$!
# `wait -n` and `kubectl wait --for=condition=failed` both exit 0 when they see their condition,
# so neither says whether the Job SUCCEEDED. Only the Job's own status decides (backoffLimit is 0).
wait -n "$done_pid" "$failed_pid" || true
kill "$done_pid" "$failed_pid" 2>/dev/null || true
succeeded="$(kubectl -n "$NS" get job eval -o jsonpath='{.status.succeeded}' 2>/dev/null || true)"
failed="$(kubectl -n "$NS" get job eval -o jsonpath='{.status.failed}' 2>/dev/null || true)"
job_ok=0
if [ "$succeeded" = 1 ] && { [ -z "$failed" ] || [ "$failed" = 0 ]; }; then job_ok=1; fi

if ! result="$(EVAL_RESULTS_DIR="${EVAL_RESULTS_DIR:-eval/results}" ./scripts/eval_results.sh | tail -n1)"; then
  kubectl -n "$NS" logs job/eval --tail=30 >&2 || true
  exit 1
fi
gate=0
./scripts/check_eval.sh "$result" || gate=$?
if [ "$job_ok" -ne 1 ]; then
  echo "evaluation job did not finish cleanly (too many errored items?): see $result" >&2
  [ "$gate" -ne 0 ] || gate=1
fi
exit "$gate"
