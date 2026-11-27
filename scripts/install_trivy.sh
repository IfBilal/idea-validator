#!/usr/bin/env bash
# Install a pinned Trivy from its GitHub release asset and verify the published sha256.
# CI uses this instead of the trivy-action installer, which scrapes github.com/.../releases/tag/<v>
# and fails whenever that web page is unavailable (it returned 504 for every version on 2026-11-27).
# Usage: scripts/install_trivy.sh v0.76.2 [install-dir]
set -euo pipefail

version="${1:?usage: install_trivy.sh vX.Y.Z [dir]}"
dir="${2:-${RUNNER_TEMP:-/tmp}/trivy-bin}"
number="${version#v}"
archive="trivy_${number}_Linux-64bit.tar.gz"
base="https://github.com/aquasecurity/trivy/releases/download/${version}"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
curl -sSfL --retry 5 --retry-delay 3 --retry-all-errors -o "$work/$archive" "$base/$archive"
curl -sSfL --retry 5 --retry-delay 3 --retry-all-errors -o "$work/checksums.txt" "$base/trivy_${number}_checksums.txt"
(cd "$work" && grep " ${archive}\$" checksums.txt | sha256sum --check --strict -)
mkdir -p "$dir"
tar -xzf "$work/$archive" -C "$dir" trivy
"$dir/trivy" --version | head -1
if [ -n "${GITHUB_PATH:-}" ]; then echo "$dir" >> "$GITHUB_PATH"; fi
