#!/usr/bin/env bash
# P11-T02: constant concurrent load through the public ingress (for resource measurement and the HPA demo).
# Each worker registers a user once, then loops: login, read profile, health. Usage: scripts/load_test.sh [SECONDS=60]
set -uo pipefail
BASE="${SMOKE_BASE_URL:-http://127.0.0.1}"
DURATION="${1:-60}"
WORKERS="${LOAD_WORKERS:-8}"
[[ "$DURATION" =~ ^[0-9]{1,4}$ ]] || { echo "seconds must be a number" >&2; exit 2; }

worker() {
  local email pass token end
  email="load-$1-$(head -c4 /dev/urandom | od -An -tx1 | tr -d ' \n')@example.com"
  pass="Load-$(head -c9 /dev/urandom | od -An -tx1 | tr -d ' \n')-Aa1"
  curl -s -m 10 -o /dev/null -H 'content-type: application/json' -d "{\"email\":\"$email\",\"password\":\"$pass\"}" "$BASE/api/auth/register"
  end=$((SECONDS + DURATION))
  while [ "$SECONDS" -lt "$end" ]; do
    token="$(curl -s -m 10 -H 'content-type: application/json' -d "{\"email\":\"$email\",\"password\":\"$pass\"}" "$BASE/api/auth/login" | sed -n 's/.*"access_token":"\([^"]*\)".*/\1/p')"
    [ -n "$token" ] && curl -s -m 10 -o /dev/null -H "authorization: Bearer $token" "$BASE/api/profile"
    curl -s -m 10 -o /dev/null "$BASE/api/health"
  done
}
for i in $(seq 1 "$WORKERS"); do worker "$i" & done
wait
echo "load finished: $WORKERS workers, ${DURATION}s" >&2
