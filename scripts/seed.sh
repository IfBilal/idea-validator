#!/usr/bin/env bash
# P3-T09: seed a cluster from the committed card snapshot (docs/harness/10 section 5).
# Usage: ./scripts/seed.sh [TAG]   (TAG defaults to the commit SHA; never "latest").
# Builds the ingestion image (it carries data/snapshots), pushes it to the local registry, then runs
# the `seed` Job (k8s/jobs/seed.yaml), which restores the newest snapshot through M6 POST /ingest.
# The Job reads INTERNAL_TOKEN and INGEST_TOKEN from the app-secrets Secret (make secrets).
# Safe to repeat: M6 upserts by URL, so a second run adds nothing.
set -euo pipefail
cd "$(dirname "$0")/.."
source "$(dirname "$0")/lib/kube_guard.sh"
export KUBECONFIG="$PROJECT_KUBECONFIG"
use_project_cluster || exit 1

TAG="${1:-$(git rev-parse --short=7 HEAD)}"
NS=idea-validator
if ! [[ "$TAG" =~ ^[A-Za-z0-9._-]{1,128}$ ]] || [ "$TAG" = latest ]; then
  echo "refusing image tag '$TAG': use a commit SHA or 'dev', never 'latest'" >&2
  exit 1
fi
if ! compgen -G "data/snapshots/cards-*.jsonl.gz" >/dev/null; then
  echo "no data/snapshots/cards-*.jsonl.gz to seed from: run 'python -m ingestion snapshot' first" >&2
  exit 1
fi

IMAGE="localhost:5001/ingestion:${TAG}"
docker build -q -f jobs/ingestion/Dockerfile -t "$IMAGE" .
docker push -q "$IMAGE"
kubectl -n "$NS" rollout status deploy/kb --timeout=300s
kubectl -n "$NS" delete job seed --ignore-not-found >/dev/null
# a commit-SHA tag never changes, so a cached image is safe; any other tag must be re-pulled
POLICY=Always
if [[ "$TAG" =~ ^[0-9a-f]{7,40}$ ]]; then POLICY=IfNotPresent; fi
sed -e "s#image: idea-validator/ingestion:unset#image: ${IMAGE}#" \
    -e "s#imagePullPolicy: Always#imagePullPolicy: ${POLICY}#" k8s/jobs/seed.yaml | kubectl apply -f -
if ! kubectl -n "$NS" wait --for=condition=complete job/seed --timeout=900s; then
  kubectl -n "$NS" logs job/seed --tail=30 >&2 || true
  exit 1
fi
kubectl -n "$NS" logs job/seed --tail=1
