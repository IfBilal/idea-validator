#!/usr/bin/env bash
# NetworkPolicy test (Guide §9.3). Screenshot the output for the report.
set -uo pipefail
NS=idea-validator
probe='import httpx,sys
try:
    r=httpx.get("https://api.github.com",timeout=5); print("REACHED", r.status_code); sys.exit(0)
except Exception as e:
    print("BLOCKED", type(e).__name__); sys.exit(1)'
echo "== from kb (non-M5): expect BLOCKED =="
kubectl exec -n $NS deploy/kb -- python -c "$probe"; kb=$?
echo "== from tools (M5): expect REACHED =="
kubectl exec -n $NS deploy/tools -- python -c "$probe"; tools=$?
if [[ $kb -ne 0 && $tools -eq 0 ]]; then echo "NETPOL TEST PASS"; exit 0; else echo "NETPOL TEST FAIL"; exit 1; fi
