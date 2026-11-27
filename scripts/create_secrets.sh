#!/usr/bin/env bash
# P1-T06: create/update the app-secrets Secret on the project's kind cluster from the git-ignored .env.
# Safe to re-run (existing random values are kept). Values are never printed or put on a command line.
set -euo pipefail
cd "$(dirname "$0")/.."
source "$(dirname "$0")/lib/kube_guard.sh"
export KUBECONFIG="$PROJECT_KUBECONFIG"
use_project_cluster || exit 1
exec python3 scripts/create_secrets.py
