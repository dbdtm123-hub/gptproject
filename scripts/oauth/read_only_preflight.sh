#!/usr/bin/env bash
# Read-only, explicit context; no Secret data, pod logs, kubeconfig, env values or exec.
set -euo pipefail
: "${AUTOLOG_CONTEXT:?Set the known production context explicitly}"
k=(kubectl --context "$AUTOLOG_CONTEXT" --request-timeout=15s)
"${k[@]}" version --client
"${k[@]}" get nodes -o custom-columns='NAME:.metadata.name,ARCH:.status.nodeInfo.architecture,CPU:.status.allocatable.cpu,MEMORY:.status.allocatable.memory'
"${k[@]}" -n lifelog get deployment -o custom-columns='NAME:.metadata.name,READY:.status.readyReplicas,IMAGES:.spec.template.spec.containers[*].image'
"${k[@]}" -n lifelog get pods --show-labels
"${k[@]}" -n lifelog get service,endpointslice,pvc
"${k[@]}" -n lifelog get networkpolicy -o yaml
"${k[@]}" -n lifelog get ciliumnetworkpolicy -o yaml
# Only Secret names/types; NEVER -o yaml/json on Secrets.
"${k[@]}" -n lifelog get secret -o custom-columns='NAME:.metadata.name,TYPE:.type'
# Only names of backend env vars and their Secret refs, no literal env values.
"${k[@]}" -n lifelog get deployment lifelog-backend -o jsonpath='{range .spec.template.spec.containers[*]}{.name}{"\n"}{range .env[*]}{.name}{" -> "}{.valueFrom.secretKeyRef.name}{"/"}{.valueFrom.secretKeyRef.key}{"\n"}{end}{end}'
echo 'Preflight completed. Review Tunnel/Access/DNS privately; do not dump tunnel tokens or credentials.'
