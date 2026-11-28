#!/usr/bin/env bash
# P1-T09: smoke test of the deployed stack through the public ingress (http://127.0.0.1). Exit 1 on any failure.
# Real round trip: register -> login -> save profile -> read it back = ingress, gateway, profile, Postgres, Redis.
set -uo pipefail
cd "$(dirname "$0")/.."
source "$(dirname "$0")/lib/kube_guard.sh"
export KUBECONFIG="$PROJECT_KUBECONFIG"
use_project_cluster || exit 1

NS=idea-validator
BASE="${SMOKE_BASE_URL:-http://127.0.0.1}"
FAILED=0
pass() { echo "PASS  $*"; }
fail() { echo "FAIL  $*"; FAILED=1; }
code() { curl -s -o /dev/null -w '%{http_code}' -m 10 "$@"; }
check() { local want="$1" got="$2" what="$3"; [ "$got" = "$want" ] && pass "$what ($got)" || fail "$what: expected $want, got $got"; }

# 1. everything the manifests promise is actually available
not_ready="$(kubectl -n "$NS" get deploy -o json | python3 -c '
import json, sys
bad = [d["metadata"]["name"] for d in json.load(sys.stdin)["items"]
       if (d["status"].get("availableReplicas") or 0) < d["spec"]["replicas"]]
print(" ".join(bad))')"
[ -z "$not_ready" ] && pass "all Deployments have their replicas available" || fail "Deployments not available: $not_ready"
for s in postgres redis redis-tools qdrant; do
  r="$(kubectl -n "$NS" get sts "$s" -o jsonpath='{.status.readyReplicas}' 2>/dev/null)"
  [ "${r:-0}" -ge 1 ] && pass "StatefulSet $s ready" || fail "StatefulSet $s not ready"
done
for j in migrate migrate-kb migrate-scouts migrate-agentops; do
  [ "$(kubectl -n "$NS" get job "$j" -o jsonpath='{.status.succeeded}' 2>/dev/null)" = 1 ] && pass "$j Job succeeded" || fail "$j Job did not succeed"
done

# 2. public surface: health yes, ops endpoints no
check 200 "$(code "$BASE/api/health")" "GET /api/health through the ingress"
check 404 "$(code "$BASE/api/metrics")" "GET /api/metrics is NOT exposed"
check 404 "$(code "$BASE/api/ready")" "GET /api/ready is NOT exposed"
check 401 "$(code "$BASE/api/profile")" "GET /api/profile without a token is refused"

# 3. a real user journey through gateway -> profile -> postgres/redis
EMAIL="smoke-$(head -c6 /dev/urandom | od -An -tx1 | tr -d ' \n')@example.com"
PASS="$(head -c18 /dev/urandom | od -An -tx1 | tr -d ' \n')Aa1"
json() { python3 -c "import json,sys; print(json.dumps({'email': sys.argv[1], 'password': sys.argv[2]}))" "$EMAIL" "$1"; }
H=(-H 'Content-Type: application/json')
check 201 "$(code -X POST "${H[@]}" -d "$(json "$PASS")" "$BASE/api/auth/register")" "register a new user"
check 401 "$(code -X POST "${H[@]}" -d "$(json "wrong-$PASS")" "$BASE/api/auth/login")" "login with a wrong password is refused"
TOKEN="$(curl -s -m 10 -X POST "${H[@]}" -d "$(json "$PASS")" "$BASE/api/auth/login" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("access_token",""))' 2>/dev/null)"
[ -n "$TOKEN" ] && pass "login returns a token" || fail "login returned no token"
PROFILE='{"skills":["python"],"hours_per_week":10,"weeks_available":8,"team_size":2,"compute":"laptop","goal":"course_project","interests":["devtools"]}'
check 200 "$(code -X PUT "${H[@]}" -H "Authorization: Bearer $TOKEN" -d "$PROFILE" "$BASE/api/profile")" "save a profile"
got="$(curl -s -m 10 -H "Authorization: Bearer $TOKEN" "$BASE/api/profile" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("hours_per_week"), d.get("goal"))' 2>/dev/null)"
[ "$got" = "10 course_project" ] && pass "profile read back from Postgres: $got" || fail "profile round trip: got '$got'"

[ $FAILED = 0 ] && echo "SMOKE TEST PASSED" || echo "SMOKE TEST FAILED"
exit $FAILED
