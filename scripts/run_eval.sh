#!/usr/bin/env bash
# Jenkins nightly Eval stage: start the evaluation job on the project cluster, then gate on the baseline.
# Goes through the kube guard (CI_KUBECONFIG, kind-idea-validator only) like every other cluster step.
set -euo pipefail
cd "$(dirname "$0")/.."
source "$(dirname "$0")/lib/kube_guard.sh"
use_project_cluster || exit 1

NS=idea-validator
MANIFEST="${EVAL_MANIFEST:-k8s/base/eval/eval-job.yaml}"
[ -f "$MANIFEST" ] || { echo "missing evaluation job manifest $MANIFEST" >&2; exit 2; }
kubectl apply -f "$MANIFEST" -n "$NS"
exec ./scripts/check_eval.sh "$@"
