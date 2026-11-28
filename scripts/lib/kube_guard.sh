#!/usr/bin/env bash
# Source this file. It pins kubectl to the project's own kind cluster and refuses anything else.
#
# Why: kubectl's default context on a developer laptop can be an unrelated cluster (k3s,
# minikube, a work cluster). An earlier k8s_up.sh used it and installed Calico there.
# Every project script that talks to the cluster must source this and call the guard first.
#
# The candidate kubeconfig is validated in a temp file and only then moved into place, so a refused
# kubeconfig never replaces PROJECT_KUBECONFIG and no temp file is left behind.

CLUSTER="${CLUSTER:-idea-validator}"
PROJECT_KUBECONFIG="${PROJECT_KUBECONFIG:-$HOME/.kube/${CLUSTER}.kubeconfig}"

# Reject a kubeconfig that would run a program or call a cloud auth helper (users[].exec / auth-provider).
_kube_guard_has_credential_plugin() {
  grep -Eq '^[[:space:]-]*(exec|auth-provider):' "$1"
}

# Every `server:` must be the local kind API (127.0.0.1 / localhost / the control-plane container name).
_kube_guard_servers_are_local() {
  local file="$1" server count=0
  while read -r server; do
    count=$((count + 1))
    case "$server" in
      https://127.0.0.1 | https://127.0.0.1:* | https://localhost | https://localhost:* | \
        "https://${CLUSTER}-control-plane" | "https://${CLUSTER}-control-plane:"*) ;;
      *) return 1 ;;
    esac
  done < <(sed -n 's/^[[:space:]-]*server:[[:space:]]*//p' "$file" | tr -d "\"' \r")
  [ "$count" -gt 0 ]
}

use_project_cluster() {
  local tmp context from_ci=0
  mkdir -p -m 700 "$(dirname "$PROJECT_KUBECONFIG")" || return 1  # a fresh Jenkins home has no ~/.kube
  tmp="$(mktemp "${PROJECT_KUBECONFIG}.XXXXXX")" || return 1
  chmod 600 "$tmp"
  if [ -n "${CI_KUBECONFIG:-}" ]; then
    # Jenkins has no `kind` binary: it is handed a kubeconfig (credential 'kubeconfig-kind').
    from_ci=1
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
  if _kube_guard_has_credential_plugin "$tmp"; then
    rm -f "$tmp"
    echo "refusing: kubeconfig uses a credential plugin (exec / auth-provider)" >&2
    return 1
  fi
  if [ "$from_ci" = 1 ] && ! _kube_guard_servers_are_local "$tmp"; then
    rm -f "$tmp"
    echo "refusing: CI_KUBECONFIG server is not the local kind API (127.0.0.1 / localhost)" >&2
    return 1
  fi
  context="$(KUBECONFIG="$tmp" kubectl config current-context 2>/dev/null || true)"
  if [ "$context" != "kind-$CLUSTER" ]; then
    rm -f "$tmp"
    echo "refusing: kubectl context is '$context', expected 'kind-$CLUSTER'" >&2
    return 1
  fi
  mv "$tmp" "$PROJECT_KUBECONFIG" || { rm -f "$tmp"; return 1; }
  export KUBECONFIG="$PROJECT_KUBECONFIG"
}
