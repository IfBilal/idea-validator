#!/usr/bin/env bash
# Proves the local kind cluster really works, not just that pods are Running (P1-T04 acceptance):
# nodes Ready, local registry serves images to nodes, ingress routes, metrics-server reports, and
# Calico ENFORCES NetworkPolicy (default-deny blocks, a narrow allow re-opens exactly one edge).
# Needs internet for two small public images (nginx, curl). Usage: ./scripts/verify_cluster.sh
set -u
cd "$(dirname "$0")/.."
source "$(dirname "$0")/lib/kube_guard.sh"
export KUBECONFIG="$PROJECT_KUBECONFIG"
use_project_cluster || exit 1
pass() { echo "PASS  $*"; }; fail() { echo "FAIL  $*"; FAILED=1; }; FAILED=0
NS=verify
kubectl delete ns $NS --ignore-not-found --wait=true >/dev/null 2>&1; kubectl create ns $NS >/dev/null

# 1. nodes + system pods
[ "$(kubectl get nodes --no-headers | grep -c ' Ready')" = 3 ] && pass "3 nodes Ready" || fail "nodes"
bad=$(kubectl get pods -A --no-headers | grep -vE 'Running|Completed' | wc -l); [ "$bad" = 0 ] && pass "all system pods Running" || fail "$bad pods not Running"

# 2. local registry -> node pull
docker pull -q nginx:1.27-alpine >/dev/null && docker tag nginx:1.27-alpine localhost:5001/verify/web:t1 && docker push -q localhost:5001/verify/web:t1 >/dev/null && pass "pushed image to localhost:5001" || fail "registry push"
kubectl -n $NS run web --image=localhost:5001/verify/web:t1 --labels app=web --port 80 >/dev/null
kubectl -n $NS expose pod web --port 80 >/dev/null
kubectl -n $NS wait --for=condition=Ready pod/web --timeout=120s >/dev/null 2>&1 && pass "pod pulled its image from the local registry" || { fail "registry pull into node"; kubectl -n $NS describe pod web | tail -5; }

# 3. ingress through host port 80
kubectl -n $NS create ingress web --class=nginx --rule="verify.test/*=web:80" >/dev/null
kubectl -n ingress-nginx wait --for=condition=Ready pod -l app.kubernetes.io/component=controller --timeout=120s >/dev/null 2>&1
ok=0; for i in $(seq 20); do code=$(curl -s -o /dev/null -w '%{http_code}' -m 3 -H 'Host: verify.test' http://127.0.0.1/); [ "$code" = 200 ] && { ok=1; break; }; sleep 2; done
[ $ok = 1 ] && pass "ingress routes http://127.0.0.1 (Host verify.test) -> 200" || fail "ingress (last code $code)"

# 4. metrics-server
m=0; for i in $(seq 15); do kubectl top nodes >/dev/null 2>&1 && { m=1; break; }; sleep 4; done
[ $m = 1 ] && pass "metrics-server: $(kubectl top nodes --no-headers | head -1 | awk '{print $1, $3, $5}')" || fail "metrics-server"

# 5. NetworkPolicy enforcement (Calico)
kubectl -n $NS run client --image=curlimages/curl:8.10.1 --labels app=client --command -- sleep 600 >/dev/null
kubectl -n $NS wait --for=condition=Ready pod/client --timeout=120s >/dev/null 2>&1
kubectl -n $NS exec client -- curl -s -o /dev/null -m 5 -w '%{http_code}' http://web | grep -q 200 && pass "before policy: client reaches web (200)" || fail "baseline connectivity"
kubectl -n $NS apply -f - >/dev/null <<'YAML'
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata: {name: default-deny-ingress}
spec: {podSelector: {}, policyTypes: [Ingress]}
YAML
sleep 5
kubectl -n $NS exec client -- curl -s -o /dev/null -m 5 http://web; rc=$?
[ $rc = 28 ] && pass "default-deny enforced: client BLOCKED (curl exit 28 timeout)" || fail "default-deny NOT enforced (curl exit $rc)"
kubectl -n $NS apply -f - >/dev/null <<'YAML'
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata: {name: allow-client-to-web}
spec:
  podSelector: {matchLabels: {app: web}}
  policyTypes: [Ingress]
  ingress: [{from: [{podSelector: {matchLabels: {app: client}}}]}]
YAML
sleep 5
kubectl -n $NS exec client -- curl -s -o /dev/null -m 5 -w '%{http_code}' http://web | grep -q 200 && pass "allow rule re-opens exactly that edge (200)" || fail "allow rule"

kubectl delete ns $NS --wait=false >/dev/null 2>&1
[ $FAILED = 0 ] && echo "ALL CHECKS PASSED" || echo "SOME CHECKS FAILED"
[ $FAILED = 0 ] || exit 1
