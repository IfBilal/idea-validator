#!/usr/bin/env bash
# Source this file. It pins kubectl to the project's own kind cluster and refuses anything else.
#
# Why: kubectl's default context on a developer laptop can be an unrelated cluster (k3s,
# minikube, a work cluster). An earlier k8s_up.sh used it and installed Calico there.
# Every project script that talks to the cluster must source this and call the guard first.

CLUSTER="${CLUSTER:-idea-validator}"
PROJECT_KUBECONFIG="${PROJECT_KUBECONFIG:-$HOME/.kube/${CLUSTER}.kubeconfig}"

use_project_cluster() {
  local tmp context
  tmp="$(mktemp "${PROJECT_KUBECONFIG}.XXXXXX")" || return 1
  chmod 600 "$tmp"
  if [ -n "${CI_KUBECONFIG:-}" ]; then
    # Jenkins has no `kind` binary: it is handed a kubeconfig (credential 'kubeconfig-kind'). The context
    # check below still refuses anything that is not the project's cluster.
    if ! cp "$CI_KUBECONFIG" "$tmp" 2>/dev/null; then
      rm -f "$tmp"
      echo "refusing: cannot read CI_KUBECONFIG" >&2
      return 1
    fi
  elif ! kind get kubeconfig --name "$CLUSTER" >"$tmp" 2>/dev/null; then
    rm -f "$tmp"
    echo "refusing: no kind cluster named '$CLUSTER' (run make k8s-up)" >&2
    return 1
  fi
  mv "$tmp" "$PROJECT_KUBECONFIG"
  export KUBECONFIG="$PROJECT_KUBECONFIG"
  context="$(kubectl config current-context 2>/dev/null || true)"
  if [ "$context" != "kind-$CLUSTER" ]; then
    echo "refusing: kubectl context is '$context', expected 'kind-$CLUSTER'" >&2
    return 1
  fi
}
