#!/usr/bin/env bash
# Build and start Jenkins in Docker for the delivery pipeline (P1-T08). Needs the kind cluster (make k8s-up) and
# the local registry. The admin password is generated once into a mode-600 file outside git.
#   scripts/jenkins_up.sh          start (or restart) Jenkins on http://127.0.0.1:8085
#   scripts/jenkins_up.sh stop     stop and remove the container (the jenkins_home volume is kept)
# Security: Jenkins listens on 127.0.0.1 only, has no anonymous access, and the admin password is mounted as a file
# (not an env var). It runs on the host network and mounts the Docker socket, which is root-equivalent on this
# machine: anyone who can log in can run commands as root here. Local-only risk acceptance: docs/decisions/ADR-012.
# JENKINS_BRANCH (default */main) is interpolated into configuration-as-code, so it is validated first.
set -euo pipefail
cd "$(dirname "$0")/.."
source "$(dirname "$0")/lib/kube_guard.sh"

NAME=idea-validator-jenkins
STATE="${XDG_CONFIG_HOME:-$HOME/.config}/idea-validator-jenkins"
PORT="${JENKINS_PORT:-8085}"
DOCKER_SOCK="${DOCKER_SOCK:-/var/run/docker.sock}"
BRANCH="${JENKINS_BRANCH:-*/main}"

if [ "${1:-}" = stop ]; then docker rm -f "$NAME" >/dev/null 2>&1 || true; echo "stopped $NAME"; exit 0; fi

if ! [[ "$BRANCH" =~ ^\*/[A-Za-z0-9._/-]+$ ]] || [[ "$BRANCH" == *..* ]]; then
  echo "refusing JENKINS_BRANCH '$BRANCH': expected */<branch> with letters, digits, . _ - /" >&2
  exit 1
fi

use_project_cluster || exit 1   # refuses any cluster that is not kind-idea-validator
mkdir -p "$STATE"; chmod 700 "$STATE"
[ -f "$STATE/admin-password" ] || { head -c 24 /dev/urandom | base64 | tr -d '/+=\n' >"$STATE/admin-password"; chmod 600 "$STATE/admin-password"; }
cp "$PROJECT_KUBECONFIG" "$STATE/kubeconfig"; chmod 600 "$STATE/kubeconfig"

docker build -q -t idea-validator/jenkins:local jenkins >/dev/null
docker rm -f "$NAME" >/dev/null 2>&1 || true
docker run -d --name "$NAME" --restart unless-stopped --network host \
  --group-add "$(stat -c %g "$DOCKER_SOCK")" \
  -v "$DOCKER_SOCK":/var/run/docker.sock \
  -v "$PWD":/repo:ro \
  -v idea-validator-jenkins-home:/var/jenkins_home \
  -v "$STATE/kubeconfig":/run/secrets/kubeconfig:ro \
  -v "$STATE/admin-password":/run/secrets/admin-password:ro \
  -e JENKINS_OPTS="--httpPort=$PORT --httpListenAddress=127.0.0.1" \
  -e JENKINS_BRANCH="$BRANCH" \
  idea-validator/jenkins:local >/dev/null

for _ in $(seq 1 60); do
  code="$(curl -s -o /dev/null -w '%{http_code}' -m 3 "http://127.0.0.1:$PORT/login" || true)"
  [ "$code" = 200 ] && break
  sleep 3
done
[ "$code" = 200 ] || { echo "Jenkins did not come up; docker logs $NAME" >&2; exit 1; }
echo "Jenkins is up: http://127.0.0.1:$PORT  user admin, password in $STATE/admin-password"
