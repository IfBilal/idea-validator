#!/usr/bin/env bash
# P1-T08 (deploy step): apply the dev overlay to the project's kind cluster at an image tag and wait until it is healthy.
# Usage: ./scripts/deploy.sh [TAG]   (TAG defaults to "dev"; never "latest"). Images must already be pushed to localhost:5001.
# Order: secrets -> apply everything -> datastores ready -> migrate Job complete -> every Deployment rolled out.
set -euo pipefail
cd "$(dirname "$0")/.."
source "$(dirname "$0")/lib/kube_guard.sh"
export KUBECONFIG="$PROJECT_KUBECONFIG"
use_project_cluster || exit 1

TAG="${1:-dev}"
NS=idea-validator
if ! [[ "$TAG" =~ ^[A-Za-z0-9._-]{1,128}$ ]] || [ "$TAG" = latest ]; then
  echo "refusing image tag '$TAG': use a commit SHA or 'dev', never 'latest'" >&2
  exit 1
fi

# kustomize refuses absolute resource paths, so the per-tag overlay lives inside the tree (git-ignored)
work="k8s/overlays/_build"
rm -rf "$work"
mkdir -p "$work"
log() { printf '\n==> %s\n' "$*"; }
diagnose() {
  echo "--- deploy failed: pods and recent events" >&2
  kubectl -n "$NS" get pods -o wide >&2 || true
  kubectl -n "$NS" get events --sort-by=.lastTimestamp 2>/dev/null | tail -15 >&2 || true
  for pod in $(kubectl -n "$NS" get pods --no-headers 2>/dev/null | awk '$3!="Running" && $3!="Completed" {print $1}' | head -4); do
    echo "--- logs $pod" >&2
    kubectl -n "$NS" logs "$pod" --tail=15 2>&1 | sed 's/^/    /' >&2 || true
  done
}
trap 'rc=$?; [ $rc -ne 0 ] && diagnose; rm -rf "$work"; exit $rc' EXIT

log "secrets"
./scripts/create_secrets.sh

log "render overlay at tag $TAG"
sed "s#newTag: dev#newTag: ${TAG}#" k8s/overlays/dev/kustomization.yaml >"$work/kustomization.yaml"
kubectl kustomize --load-restrictor=LoadRestrictionsNone "$work" >"$work/all.yaml"

log "apply (a finished migrate Job is immutable: replace it)"
kubectl -n "$NS" delete job migrate migrate-kb --ignore-not-found >/dev/null
kubectl apply -f "$work/all.yaml"

log "datastores"
for s in postgres redis redis-tools qdrant; do kubectl -n "$NS" rollout status "sts/$s" --timeout=300s; done

log "database migration"
kubectl -n "$NS" wait --for=condition=complete job/migrate job/migrate-kb --timeout=300s

log "application deployments"
for d in $(kubectl -n "$NS" get deploy -o name | sort); do kubectl -n "$NS" rollout status "$d" --timeout=300s; done

log "deployed tag $TAG"
kubectl -n "$NS" get pods --no-headers | awk '{print "  " $1, $2, $3}'
