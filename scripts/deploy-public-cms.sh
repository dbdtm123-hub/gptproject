#!/usr/bin/env bash
# Update an existing AutoLog cluster; preserve Services, Tunnel, DB/PVC and Secret values.
set -euo pipefail
cd "$(dirname "$0")/.."
for tool in git python3 podman kubectl curl; do
  command -v "$tool" >/dev/null || { echo "Required tool missing: $tool" >&2; exit 1; }
done
if [[ -n "$(git status --porcelain)" ]]; then
  echo "Commit or resolve working tree changes before building a release." >&2
  exit 1
fi
NAMESPACE="${NAMESPACE:-lifelog}"
PLATFORM="${PLATFORM:-linux/amd64}"
RELEASE_TAG="${RELEASE_TAG:-$(git rev-parse --short=12 HEAD)}"
PUBLIC_URL="${PUBLIC_URL:-https://autolog.myhomlab.win}"
[[ "$RELEASE_TAG" =~ ^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$ ]] || { echo "Invalid RELEASE_TAG" >&2; exit 1; }
python3 - "$PUBLIC_URL" <<'PY'
import sys
from urllib.parse import urlsplit
url=urlsplit(sys.argv[1])
if url.scheme!='https' or not url.hostname or url.username or url.password or url.path not in ('','/') or url.query or url.fragment:
    raise SystemExit('PUBLIC_URL must be an HTTPS origin without credentials, path or query')
PY
kubectl config current-context
kubectl -n "$NAMESPACE" get secret lifelog-app lifelog-api -o name
current_backend="$(kubectl -n "$NAMESPACE" get deployment lifelog-backend -o jsonpath='{.spec.template.spec.containers[?(@.name=="backend")].image}')"
current_frontend="$(kubectl -n "$NAMESPACE" get deployment lifelog-frontend -o jsonpath='{.spec.template.spec.containers[?(@.name=="frontend")].image}')"
repository() {
  python3 - "$1" <<'PY'
import sys
value=sys.argv[1].split('@',1)[0]
prefix,separator,name=value.rpartition('/')
registry=value.split('/',1)[0]
if not separator or not ('.' in registry or ':' in registry or registry=='localhost'):
    raise SystemExit('Current image is not registry-qualified; set BACKEND_REPO/FRONTEND_REPO explicitly')
print(prefix+separator+name.split(':',1)[0])
PY
}
BACKEND_REPO="${BACKEND_REPO:-$(repository "$current_backend")}"
FRONTEND_REPO="${FRONTEND_REPO:-$(repository "$current_frontend")}"
backend_image="$BACKEND_REPO:$RELEASE_TAG"
frontend_image="$FRONTEND_REPO:$RELEASE_TAG"
make_patch() {
  python3 - "$1" "$2" <<'PY'
import json,sys
component,image=sys.argv[1:]
if component=='backend':
    env=[{'name':key,'value':None,'valueFrom':{'secretKeyRef':{'name':'lifelog-app','key':key}}} for key in ['APP_USERNAME','APP_PASSWORD']]
    env.append({'name':'API_TOKEN','value':None,'valueFrom':{'secretKeyRef':{'name':'lifelog-api','key':'API_TOKEN'}}})
else:
    env=[{'name':key,'$patch':'delete'} for key in ['APP_USERNAME','APP_PASSWORD','API_TOKEN']]
print(json.dumps({'spec':{'template':{'spec':{'containers':[{'name':component,'image':image,'env':env}]}}}}))
PY
}
backend_patch="$(make_patch backend "$backend_image")"
frontend_patch="$(make_patch frontend "$frontend_image")"
# Check admission before spending time building or changing deployments.
kubectl -n "$NAMESPACE" patch deployment lifelog-backend --type=strategic --patch "$backend_patch" --dry-run=server >/dev/null
kubectl -n "$NAMESPACE" patch deployment lifelog-frontend --type=strategic --patch "$frontend_patch" --dry-run=server >/dev/null
printf 'Backend image: %s\nFrontend image: %s\n' "$backend_image" "$frontend_image"
podman login "${BACKEND_REPO%%/*}"
if [[ "${FRONTEND_REPO%%/*}" != "${BACKEND_REPO%%/*}" ]]; then podman login "${FRONTEND_REPO%%/*}"; fi
podman build --format oci --platform "$PLATFORM" -f backend/Dockerfile -t "$backend_image" backend/
podman build --format oci --platform "$PLATFORM" -f frontend/Dockerfile -t "$frontend_image" frontend/
podman push "$backend_image"
podman push "$frontend_image"
# Each patch changes the image and environment atomically, preserving all other deployment settings.
kubectl -n "$NAMESPACE" patch deployment lifelog-backend --type=strategic --patch "$backend_patch"
kubectl -n "$NAMESPACE" rollout status deployment/lifelog-backend --timeout=180s
kubectl -n "$NAMESPACE" patch deployment lifelog-frontend --type=strategic --patch "$frontend_patch"
kubectl -n "$NAMESPACE" rollout status deployment/lifelog-frontend --timeout=180s
curl --fail --silent --show-error "${PUBLIC_URL%/}/health/ready"
curl --fail --silent --show-error "${PUBLIC_URL%/}/api/articles" --output /dev/null
printf '\nRollout and public GET checks completed. Verify /login -> /admin in your browser.\n'
