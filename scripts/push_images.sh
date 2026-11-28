#!/usr/bin/env bash
# Jenkins "Push" stage: `make images` builds and pushes; this proves every image is really in the local
# registry at the build's tag before anything is deployed. Usage: scripts/push_images.sh <TAG>
set -euo pipefail
cd "$(dirname "$0")/.."

TAG="${1:-}"
REGISTRY="${REGISTRY:-localhost:5001}"
if ! [[ "$TAG" =~ ^[A-Za-z0-9._-]{1,128}$ ]] || [ "$TAG" = latest ]; then
  echo "refusing image tag '$TAG': use a commit SHA, never 'latest' or empty" >&2
  exit 1
fi

services="$(sed -n 's/^SERVICES := //p' Makefile)"
[ -n "$services" ] || { echo "no SERVICES line in the Makefile" >&2; exit 2; }
[ ! -f services/ui/package.json ] || services="$services ui"
[ ! -f jobs/ingestion/Dockerfile ] || services="$services ingestion"

missing=()
for name in $services; do
  tags="$(curl -fsS --max-time 10 "http://$REGISTRY/v2/$name/tags/list" 2>/dev/null || true)"
  if printf '%s' "$tags" | grep -q "\"$TAG\""; then
    echo "ok       $name:$TAG"
  else
    missing+=("$name")
  fi
done
if [ "${#missing[@]}" -gt 0 ]; then
  echo "not in $REGISTRY at tag $TAG: ${missing[*]}" >&2
  exit 1
fi
echo "all images are in $REGISTRY at $TAG"
