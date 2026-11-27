#!/usr/bin/env bash
# P1-T07 acceptance (Guide 9.3): only the tool layer (M5) can reach the internet. Screenshot the output for the report.
# Probes from three workloads, plus the internal edges the policies must close: a service may not call a service
# that is not on its call list (docs/harness/16 section 5). Pins the project's kubeconfig first.
set -uo pipefail
cd "$(dirname "$0")/.."
source "$(dirname "$0")/lib/kube_guard.sh"
export KUBECONFIG="$PROJECT_KUBECONFIG"
use_project_cluster || exit 1

NS=idea-validator
FAILED=0
probe='import httpx,sys
try:
    r=httpx.get("https://api.github.com",timeout=6); print("REACHED", r.status_code); sys.exit(0)
except Exception as e:
    print("BLOCKED", type(e).__name__); sys.exit(1)'
internal='import httpx,sys
try:
    r=httpx.get("http://%s:8000/health",timeout=5); print("REACHED", r.status_code); sys.exit(0)
except Exception as e:
    print("BLOCKED", type(e).__name__); sys.exit(1)'
# A "blocked" verdict must come from the probe itself printing BLOCKED: a failing `kubectl exec` (wrong name,
# pod not ready) must never count as a pass. Likewise "reach" needs the probe to print REACHED.
expect() { # want output what
  case "$1:$2" in
    reach:*REACHED*) echo "PASS  $3" ;;
    block:*BLOCKED*) echo "PASS  $3" ;;
    *) echo "FAIL  $3 (probe said: $(printf '%s' "$2" | tr '\n' ' ' | cut -c1-80))"; FAILED=1 ;;
  esac
}

echo "== internet egress: only tools =="
out="$(kubectl exec -n $NS deploy/tools -- python -c "$probe" 2>&1)"; expect reach "$out" "tools -> api.github.com (M5 may reach the internet)"
for svc in kb gateway validators scorer ideation; do
  out="$(kubectl exec -n $NS "deploy/$svc" -- python -c "$probe" 2>&1)"; expect block "$out" "$svc -> api.github.com blocked"
done

echo "== internal edges: closed unless on the call list =="
# (caller target expected)  allowed edges come from scripts/gen_k8s.py CALLS
edge() { # caller target want
  # shellcheck disable=SC2059
  out="$(kubectl exec -n $NS "deploy/$1" -- python -c "$(printf "$internal" "$2")" 2>&1)"; expect "$3" "$out" "$1 -> $2 ($3)"
}
edge gateway profile reach
edge orchestrator verdict reach
edge validators scorer reach
edge scorer tools reach
edge gateway verdict block
edge scouts verdict block
edge kb orchestrator block
edge tools kb block
edge critic scorer block

# negative control: a probe that cannot run must FAIL, proving the test can tell "blocked" from "broken"
out="$(kubectl exec -n $NS deploy/does-not-exist -- python -c "$probe" 2>&1)"
case "$out" in *BLOCKED*|*REACHED*) echo "FAIL  control: a missing workload produced a verdict"; FAILED=1 ;; *) echo "PASS  control: a probe that cannot run is neither blocked nor reached" ;; esac

[ $FAILED -eq 0 ] && echo "NETPOL TEST PASS" || echo "NETPOL TEST FAIL"
exit $FAILED
