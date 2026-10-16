#!/usr/bin/env bash
# Safe, explicit application image rollout. Never applies DB/Secrets/Network resources.
set -Eeuo pipefail

usage() {
  echo "Usage: $0 <40-hex-work-commit> [--apply]" >&2
  echo "Without --apply, this command only checks cluster state and shows the release plan." >&2
}

if [[ $# -lt 1 || $# -gt 2 ]]; then usage; exit 2; fi
release_sha="$1"
mode="${2:---plan}"
if [[ ! "$release_sha" =~ ^[0-9a-f]{40}$ ]]; then
  echo "Expected an exact 40-character, lowercase Git commit SHA." >&2
  exit 2
fi
if [[ "$mode" != "--plan" && "$mode" != "--apply" ]]; then usage; exit 2; fi

: "${KUBECONFIG:?Provide a dedicated, restricted kubeconfig on the deployment runner}"
command -v kubectl >/dev/null || { echo "kubectl is required" >&2; exit 1; }

namespace="lifelog"
registry="ghcr.io/dbdtm123-hub/gptproject-autolog-ci"
components=(backend image-gateway frontend)
workloads=(lifelog-backend image-gateway lifelog-frontend)
images=(
  "$registry/backend:sha-$release_sha"
  "$registry/image-gateway:sha-$release_sha"
  "$registry/frontend:sha-$release_sha"
)

echo "Cluster context: $(kubectl config current-context)"
for i in 0 1 2; do
  current_containers="$(kubectl --request-timeout=15s -n "$namespace" get deployment "${workloads[$i]}" -o jsonpath='{.spec.template.spec.containers[*].name}')"
  if [[ " $current_containers " != *" ${components[$i]} "* ]]; then
    echo "Refusing: deployment/${workloads[$i]} does not have container/${components[$i]} (found: $current_containers)" >&2
    exit 1
  fi
  echo "Plan: deployment/${workloads[$i]} container/${components[$i]} -> ${images[$i]}"
done
echo "No database migration, Secret, ConfigMap, PVC, Service or NetworkPolicy changes are included."

if [[ "$mode" == "--plan" ]]; then
  echo "PLAN ONLY: no Kubernetes resources changed. --apply requires a separate explicit confirmation."
  exit 0
fi
if [[ "${AUTOLOG_CONFIRM_DEPLOY:-}" != "DEPLOY-LIFELOG" ]]; then
  echo "Refusing deployment: set AUTOLOG_CONFIRM_DEPLOY=DEPLOY-LIFELOG after approval." >&2
  exit 2
fi

# The caller is responsible for verifying the GitHub Actions CI run and
# confirming a backup + backward-compatible migration for this commit.
changed=()
rollback() {
  status=$?
  trap - ERR
  echo "Release failed (status=$status); attempting application Deployment rollbacks." >&2
  for (( i=${#changed[@]}-1; i>=0; i-- )); do
    workload="${changed[$i]}"
    kubectl -n "$namespace" rollout undo "deployment/$workload" || true
    kubectl -n "$namespace" rollout status "deployment/$workload" --timeout=300s || true
  done
  exit "$status"
}
trap rollback ERR

for i in 0 1 2; do
  workload="${workloads[$i]}"
  container="${components[$i]}"
  kubectl -n "$namespace" set image "deployment/$workload" "$container=${images[$i]}"
  changed+=("$workload")
  kubectl -n "$namespace" rollout status "deployment/$workload" --timeout=300s
done

trap - ERR
echo "All three application Deployments rolled out successfully."
echo "Check actual public HTTP routes separately; a healthy Deployment does not prove end-to-end behavior."
