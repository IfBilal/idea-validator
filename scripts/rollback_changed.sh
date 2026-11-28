#!/usr/bin/env bash
# Jenkins post-failure step: undo the Deployments THIS build changed, nothing else.
# A Deployment counts as changed when its image runs the build's tag and it has an earlier revision.
# Usage: scripts/rollback_changed.sh <TAG>
set -euo pipefail
cd "$(dirname "$0")/.."
source "$(dirname "$0")/lib/kube_guard.sh"
export KUBECONFIG="$PROJECT_KUBECONFIG"
use_project_cluster || exit 1

TAG="${1:-}"
NS=idea-validator
if ! [[ "$TAG" =~ ^[A-Za-z0-9._-]{1,128}$ ]] || [ "$TAG" = latest ]; then
  echo "refusing image tag '$TAG'" >&2
  exit 1
fi

rolled=()
while read -r name image revision; do
  [ -n "${name:-}" ] || continue
  case "$image" in *":$TAG") ;; *) continue ;; esac
  if [ "${revision:-1}" -lt 2 ]; then
    echo "skip     $name: first revision, nothing to roll back to"
    continue
  fi
  echo "rollback $name (revision $revision, image $image)"
  kubectl -n "$NS" rollout undo "deployment/$name"
  kubectl -n "$NS" rollout status "deployment/$name" --timeout=180s
  rolled+=("$name")
done < <(kubectl -n "$NS" get deploy -o jsonpath='{range .items[*]}{.metadata.name} {.spec.template.spec.containers[0].image} {.metadata.annotations.deployment\.kubernetes\.io/revision}{"\n"}{end}')

echo "rolled back: ${rolled[*]:-nothing}"
