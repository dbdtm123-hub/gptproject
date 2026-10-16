#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
: "${REGISTRY:?Set REGISTRY=ghcr.io/your-github-user}"
PLATFORM="${PLATFORM:-linux/amd64}"
for component in backend frontend; do
  podman build --format oci --platform "$PLATFORM" \
    -f "$component/Dockerfile" -t "autolog/$component:latest" "$component"
  podman tag "autolog/$component:latest" "$REGISTRY/autolog/$component:latest"
  podman push "$REGISTRY/autolog/$component:latest"
done
