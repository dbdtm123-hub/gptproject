# Autolog — AI 자동 라이프로그

Next.js UI, FastAPI API, PostgreSQL과 Alembic을 포함한다. 자연어 AI 분류, 여러 기록 분리,
분류 결과 수정·일괄 저장, 카테고리 필터, 타임라인, 상세 조회·삭제를 구현했다.
OpenAI는 structured JSON을 반환하고 백엔드가 검증한 뒤에만 저장한다.

운영: **Ubuntu VM 3대 / Kubernetes / containerd / Cilium**, 이미지 빌드: **Podman**.
Docker Compose는 개발·통합 테스트 전용이다. Kubernetes 노드에 Docker daemon을 설치하지 않는다.

## 구성과 환경변수

- frontend/Dockerfile: Next.js standalone 다단계 production 이미지, 비루트 실행.
- backend/Dockerfile: FastAPI production 이미지, 고정 의존성·해시 검증, 비루트 실행.
- k8s/platform, database, migration, backend, frontend, networking, ingress: 순서별 매니페스트.
- scripts/build-images.sh: Podman build/tag/push.
- scripts/render-k8s.py: GHCR 이미지, PVC/emptyDir, NodePort/Ingress 선택을 적용한 YAML 생성.
- k8s/examples: Secret 구조와 기본 StorageClass 없는 환경의 local PV 예시.

브라우저 → Frontend NodePort(30080) → Next.js 서버 /api 프록시 → Backend ClusterIP → PostgreSQL.
OpenAI는 backend에서만 호출한다. 프론트엔드 서버의 Basic 인증으로 페이지와 /api를 보호한다.
비밀번호 설정이 빠지면 보호된 경로는 503으로 닫힌다. health endpoint는 인증 없이 상태만 반환한다.

| 변수 | 주입 대상 | 저장 위치 |
| --- | --- | --- |
| DATABASE_URL | backend, migration | lifelog-db Secret |
| POSTGRES_PASSWORD | PostgreSQL | lifelog-db Secret |
| POSTGRES_DB, POSTGRES_USER | PostgreSQL | ConfigMap (기본 lifelog) |
| OPENAI_API_KEY | backend만 | lifelog-openai Secret |
| OPENAI_MODEL | backend | ConfigMap (기본 gpt-4o-mini) |
| BACKEND_URL | frontend 서버 | ConfigMap (http://lifelog-backend:8000) |
| APP_USERNAME, APP_PASSWORD | frontend 서버 | lifelog-app Secret |
| CORS_ORIGINS | backend | ConfigMap, JSON 배열 |

키와 비밀번호를 YAML, Git, 이미지, NEXT_PUBLIC 변수에 넣지 않는다. .env.example의 빈 값은
보안 설정으로 채우며 실제 .env는 Git에서 제외한다. Kubernetes는 .env를 읽지 않고 Secret/ConfigMap을 쓴다.
OpenAI Secret은 수동 입력 테스트에만 생략 가능하다. 생략하면 AI 분류는 503이고 직접 분류·저장은 작동한다.

## 1. 클러스터 확인

관리용 kubeconfig를 가진 호스트의 저장소 루트에서 실행한다. kubectl 버전은 클러스터와
minor 차이 1 이하로 맞춘다. Podman 5.x와 Python 3.12+를 사용한다.

~~~bash
kubectl config current-context
kubectl get nodes -o custom-columns='NAME:.metadata.name,ARCH:.status.nodeInfo.architecture,RUNTIME:.status.nodeInfo.containerRuntimeVersion'
kubectl get pods -n kube-system
kubectl get storageclass
kubectl get crd ciliumnetworkpolicies.cilium.io
~~~

노드가 Ready, 런타임이 containerd, Cilium/CoreDNS가 정상인지 확인한다.
기본 접속은 NodePort이며 Cilium Ingress나 Octavia 설치가 필요 없다.
예시는 amd64 이미지다. 노드가 ARM이면 PLATFORM과 빌드 호스트를 맞추거나 multiarch 이미지를 사용한다.

## 2. GHCR 로그인과 Podman 이미지 빌드/push

아직 레지스트리 소유자가 정해지지 않았으므로 your-github-user를 실제 소문자 GitHub 사용자/조직으로 바꾼다.
GHCR 로그인 비밀번호에는 read:packages/write:packages 권한을 가진 토큰을 대화형으로 입력한다.
토큰을 명령 인자나 채팅에 넣지 않는다. Kubernetes pull에는 read 권한만 가진 자격증명을 권장한다.

~~~bash
set -euo pipefail
umask 077
export GHCR_OWNER=your-github-user
export REGISTRY="ghcr.io/$GHCR_OWNER"
auth_dir="$(mktemp -d)"
export REGISTRY_AUTH_FILE="$auth_dir/auth.json"
podman login --authfile "$REGISTRY_AUTH_FILE" ghcr.io

# 실제 실행 명령
podman build --format oci --platform linux/amd64 -f backend/Dockerfile -t autolog/backend:latest backend
podman build --format oci --platform linux/amd64 -f frontend/Dockerfile -t autolog/frontend:latest frontend
podman tag autolog/backend:latest "$REGISTRY/autolog/backend:latest"
podman tag autolog/frontend:latest "$REGISTRY/autolog/frontend:latest"
podman push "$REGISTRY/autolog/backend:latest"
podman push "$REGISTRY/autolog/frontend:latest"

# 위 build/tag/push를 반복할 때는 다음 helper도 사용할 수 있다.
# bash scripts/build-images.sh
~~~

기본 매니페스트 이미지는 autolog/backend:latest, autolog/frontend:latest다.
renderer가 GHCR prefix를 붙이고 imagePullPolicy는 Always다.
같은 latest 태그로 push한 후 apply만 하면 기존 Pod는 교체되지 않으므로 업데이트 시 rollout restart가 필요하다.
릴리스 재현성을 높이려면 renderer의 --backend-image/--frontend-image에 GHCR digest 또는 고유 태그를 전달한다.
API와 migration은 같은 backend image를 사용한다. deployment 도중 latest를 다시 push하지 않는다.

기존 Dockerfile의 CA secret mount는 프록시 빌드에 사용할 수 있다.
필요하면 Podman에 --secret id=build_ca,src=/path/to/trusted-ca-bundle.crt와 기존 프록시 build args를 전달한다.
TLS 검증을 해제하지 않는다. [containerd 사설 레지스트리 CA 설정](docs/kubernetes-deployment.md)도 참고한다.
GHCR은 공인 CA를 사용하므로 일반적으로 별도 node CA 설정이 필요 없다.

## 3. PostgreSQL 저장 방식 선택과 YAML 생성

운영 기본은 StatefulSet + PVC이며 PostgreSQL 17-bookworm을 사용한다.
Namespace와 리소스 이름은 기존 lifelog를 유지한다.

### A. 기본 StorageClass가 있는 경우

~~~bash
export RELEASE_DIR=/tmp/autolog-release-01
python scripts/render-k8s.py --registry "$REGISTRY" \
  --hostname autolog.example.com --output "$RELEASE_DIR"
~~~

도메인은 현재 예시값이다. NodePort 접속에는 도메인이나 DNS 등록이 필요 없다.
PVC에는 storageClassName을 생략해 클러스터 기본값을 사용한다. 특정 Class는 --storage-class 이름으로 지정한다.
기본 Class가 없으면 PVC가 Pending이므로 B 또는 C를 선택한다.

### B. 기본 StorageClass 없는 환경: 영속 local PV

선택한 Ubuntu VM에서 실제 DB 경로를 준비한다.

~~~bash
sudo install -d -o 999 -g 999 -m 0770 /var/lib/autolog/postgres
~~~

관리 호스트에서 k8s/examples/local-storage.yaml.example을 Git 밖으로 복사하고
REPLACE_WITH_UBUNTU_NODE_NAME을 kubectl get nodes의 실제 hostname label 값으로 수정한다.

~~~bash
cp k8s/examples/local-storage.yaml.example /tmp/autolog-local-storage.yaml
# /tmp/autolog-local-storage.yaml의 node hostname과 경로를 실제 VM에 맞춰 편집
kubectl apply --dry-run=server -f /tmp/autolog-local-storage.yaml
kubectl apply -f /tmp/autolog-local-storage.yaml
export RELEASE_DIR=/tmp/autolog-release-local-01
python scripts/render-k8s.py --registry "$REGISTRY" --hostname autolog.example.com \
  --storage-class autolog-local --output "$RELEASE_DIR"
~~~

이 PV는 특정 VM에 고정된다. Pod 재생성에도 데이터는 유지되지만 VM/디스크 장애 시 자동 failover하지 않는다.
PV 용량(예시 20Gi)과 renderer의 --storage-size를 맞춘다. 이후 Cinder/다른 CSI를 설치하면 해당 Class로 별도 이전한다.

### C. 빠른 테스트만: emptyDir

~~~bash
export RELEASE_DIR=/tmp/autolog-release-ephemeral-01
python scripts/render-k8s.py --registry "$REGISTRY" --hostname autolog.example.com \
  --database-storage emptydir --output "$RELEASE_DIR"
~~~

StorageClass/PVC가 필요 없다. **DB Pod 삭제·재스케줄 시 데이터가 사라지므로 운영 데이터에 쓰지 않는다.**
PVC와 emptyDir 간 전환은 StatefulSet의 immutable 필드 변경이므로 기존 배포에 그대로 apply하지 않는다.
전환 시 먼저 DB를 백업하고 별도 이전 계획을 세운다. 기본 production manifest는 항상 PVC다.

## 4. Namespace·설정·Secret 준비

생성된 파일 전체를 한 번에 apply하지 않는다. DB 준비와 migration 완료를 각 단계에서 기다린다.

~~~bash
kubectl apply --dry-run=server -f "$RELEASE_DIR/01-platform.yaml"
kubectl apply -f "$RELEASE_DIR/01-platform.yaml"

# GHCR 이미지가 private인 경우
kubectl -n lifelog create secret generic registry-credentials \
  --type=kubernetes.io/dockerconfigjson \
  --from-file=.dockerconfigjson="$REGISTRY_AUTH_FILE" \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl -n lifelog patch serviceaccount lifelog --type=merge \
  -p '{"imagePullSecrets":[{"name":"registry-credentials"}]}'
~~~

k8s/examples/secrets.yaml.example에는 placeholder만 있다. 실제 Secret은 secret manager에서 생성하거나
다음 interactive helper로 Git 밖의 새 디렉터리에 만든다. 입력한 값을 출력하지 않는다.

~~~bash
export SECRET_DIR="/tmp/autolog-secrets-$(date -u +%Y%m%dT%H%M%SZ)"
python scripts/create-secret-files.py "$SECRET_DIR"

kubectl -n lifelog create secret generic lifelog-db \
  --from-file=POSTGRES_PASSWORD="$SECRET_DIR/POSTGRES_PASSWORD" \
  --from-file=DATABASE_URL="$SECRET_DIR/DATABASE_URL" \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl -n lifelog create secret generic lifelog-app \
  --from-file=APP_USERNAME="$SECRET_DIR/APP_USERNAME" \
  --from-file=APP_PASSWORD="$SECRET_DIR/APP_PASSWORD" \
  --dry-run=client -o yaml | kubectl apply -f -

# helper에서 실제 키를 입력한 경우에만. 키 없이 수동 입력 테스트는 가능하다.
if test -f "$SECRET_DIR/OPENAI_API_KEY"; then
  kubectl -n lifelog create secret generic lifelog-openai \
    --from-file=OPENAI_API_KEY="$SECRET_DIR/OPENAI_API_KEY" \
    --dry-run=client -o yaml | kubectl apply -f -
fi
~~~

최초 DB 초기화에서만 비밀번호를 정한다. 기존 DB에 Secret만 새 암호로 교체하면 접속이 실패한다.
반복 배포는 기존 DB credentials를 유지한다. helper는 URL 예약 문자를 인코딩한다.
Secret base64는 암호화가 아니므로 etcd encryption과 RBAC를 적용한다.
Secret 생성 후 위 임시 파일을 삭제하고 안전한 비밀번호/secret manager에 보관한다.

## 5. DB → migration → backend → frontend → 네트워크

~~~bash
kubectl apply --dry-run=server -f "$RELEASE_DIR/02-database.yaml"
kubectl apply -f "$RELEASE_DIR/02-database.yaml"
kubectl -n lifelog rollout status statefulset/lifelog-postgres --timeout=300s
kubectl -n lifelog get pvc
kubectl -n lifelog exec lifelog-postgres-0 -- sh -c \
  'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT 1"'

# 최초 배포: 동일 이름의 이전 Job이 없어야 한다.
kubectl apply --dry-run=server -f "$RELEASE_DIR/03-migration.yaml"
kubectl apply -f "$RELEASE_DIR/03-migration.yaml"
kubectl -n lifelog wait --for=condition=complete job/lifelog-migrate --timeout=300s
kubectl -n lifelog logs job/lifelog-migrate

# migration 성공 후에만 진행한다.
kubectl apply --dry-run=server -f "$RELEASE_DIR/04-backend.yaml"
kubectl apply -f "$RELEASE_DIR/04-backend.yaml"
kubectl -n lifelog rollout status deployment/lifelog-backend --timeout=300s

kubectl apply --dry-run=server -f "$RELEASE_DIR/05-frontend.yaml"
kubectl apply -f "$RELEASE_DIR/05-frontend.yaml"
kubectl -n lifelog rollout status deployment/lifelog-frontend --timeout=300s

kubectl apply --dry-run=server -f "$RELEASE_DIR/06-networking.yaml"
kubectl apply -f "$RELEASE_DIR/06-networking.yaml"
kubectl -n lifelog get pods,svc,pvc
~~~

emptyDir에서는 DB용 PVC가 없는 것이 정상이다. Job 실패 시 원인을 조사하고 API를 업데이트하지 않는다.
API/UI는 기본 2 replicas이며 CPU/memory requests·limits, startup/readiness/liveness probe, PDB를 포함한다.
DB liveness와 API liveness는 각각 분리되고 frontend readiness는 backend 연결과 로그인 설정을 확인한다.

NetworkPolicy는 frontend→backend→DB만 허용하며 CiliumNetworkPolicy는 frontend의 NodePort/Ingress 접속과
backend의 api.openai.com HTTPS를 허용한다. DNS 규칙은 kube-system/kube-dns 기준이다.
NodeLocal DNS나 다른 DNS labels를 쓰면 정책을 맞춘다. OpenAI 접속 문제가 있으면 Cilium DNS proxy/FQDN 정책도 확인한다.
서로 다른 Namespace의 API/DB 또는 프록시 경유 외부 통신을 사용할 때는 정책을 함께 변경한다.

## 6. NodePort 외부 접속 확인

노드 하나에 OpenStack Floating IP 또는 접근 가능한 라우팅 주소가 필요하다.
해당 VM의 Security Group/Ubuntu 방화벽에서 **본인 접속 IP에 한해 TCP 30080**을 허용한다.
공유 기본 정책을 덮어쓰지 않는다. Kubernetes 내부망을 Floating IP로 착각하지 않는다.

~~~bash
kubectl -n lifelog get service lifelog-frontend
kubectl get nodes -o wide
export NODE_IP=YOUR_REACHABLE_VM_IP
curl --fail "http://$NODE_IP:30080/health/live"
curl --fail "http://$NODE_IP:30080/health/ready"
# 비밀번호는 curl이 대화형으로 묻는다.
curl --fail --user YOUR_APP_USERNAME "http://$NODE_IP:30080/api/entries"
~~~

브라우저에서 http://VM_IP:30080을 열고 Secret의 앱 계정으로 로그인한다.
먼저 직접 분류로 기록 생성·필터·상세·삭제를 확인한 뒤, 실제 OpenAI Secret이 있으면 AI 분류도 확인한다.
NodePort HTTP는 테스트용이며 Basic 암호와 기록이 암호화되지 않는다.
민감한 개인 기록의 실제 외부 운영은 다음 TLS 옵션이나 별도의 신뢰 TLS reverse proxy를 준비한다.

내부 확인은 다음 명령으로 localhost에만 연결한다.

~~~bash
kubectl -n lifelog port-forward --address 127.0.0.1 service/lifelog-frontend 13000:3000
# 별도 터미널
curl --fail http://127.0.0.1:13000/health/ready
curl --fail --user YOUR_APP_USERNAME http://127.0.0.1:13000/api/categories
~~~

## 7. 선택 옵션: Cilium Ingress + TLS

Cilium Ingress가 활성화된 이후 적용한다. 기존 Cilium Helm values를 보존하고 해당 버전에 맞게
ingressController와 필요한 kubeProxyReplacement/Envoy/L7 설정을 활성화한다.
Helm release를 새 기본값으로 덮어쓰지 않는다. IngressClass cilium과 controller 상태를 확인한다.

실제 도메인과 TLS 인증서를 준비하고 새 디렉터리에 --frontend-service ClusterIP로 다시 render한다.
이 구성은 API를 직접 공개하지 않고 인증이 있는 frontend로 모든 경로를 보낸다.

~~~bash
export LIFELOG_HOST=autolog.your-domain.example
export TLS_RELEASE=/tmp/autolog-release-tls-01
python scripts/render-k8s.py --registry "$REGISTRY" --hostname "$LIFELOG_HOST" \
  --storage-class autolog-local --frontend-service ClusterIP --output "$TLS_RELEASE"
# emptyDir 테스트라면 --storage-class 대신 --database-storage emptydir를 유지한다.
kubectl -n lifelog create secret tls lifelog-tls \
  --cert=/secure/path/fullchain.pem --key=/secure/path/privkey.pem \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -f "$TLS_RELEASE/01-platform.yaml"
kubectl apply -f "$TLS_RELEASE/05-frontend.yaml"
kubectl apply --dry-run=server -f "$TLS_RELEASE/07-ingress.yaml"
kubectl apply -f "$TLS_RELEASE/07-ingress.yaml"
kubectl -n lifelog get ingress,svc
~~~

dedicated Cilium Ingress는 LoadBalancer Service를 생성한다. OpenStack CCM/Octavia 등 LB 제공자가 없으면
EXTERNAL-IP가 Pending이다. 이 경우 TLS Ingress Service를 명시적으로 NodePort로 바꾸는 방법도 있다.
실제 생성된 Service 이름을 get svc로 먼저 확인한다(통상 cilium-ingress-lifelog).

~~~bash
kubectl -n lifelog patch service cilium-ingress-lifelog --type=merge \
  -p '{"spec":{"type":"NodePort"}}'
kubectl -n lifelog get service cilium-ingress-lifelog \
  -o jsonpath='{range .spec.ports[*]}{.name}{" "}{.port}{" -> "}{.nodePort}{"\n"}{end}'
~~~

확인된 HTTPS nodePort를 접속 IP에만 허용하고 https://도메인:HTTPS_NODEPORT로 연결한다.
LB를 쓰면 DNS를 VIP에 연결해 기본 HTTPS 443으로 접속한다. DNS 등록 전 테스트는 다음과 같다.

~~~bash
curl --fail --resolve "$LIFELOG_HOST:443:YOUR_LB_IP" "https://$LIFELOG_HOST/health/ready"
curl --fail --user YOUR_APP_USERNAME "https://$LIFELOG_HOST/api/entries"
~~~

인증 없는 /api 요청이 401인지 확인한다. curl -k로 인증서 검증을 생략하지 않는다.
NodePort 공개 서비스가 필요 없어지면 ClusterIP 적용과 함께 Security Group의 30080 규칙을 제거한다.

## 8. 장애 확인·업데이트·백업

~~~bash
kubectl -n lifelog get pods,svc,pvc,ingress
kubectl -n lifelog get events --sort-by=.lastTimestamp
kubectl -n lifelog logs deployment/lifelog-frontend --all-pods=true --tail=100
kubectl -n lifelog logs deployment/lifelog-backend --all-pods=true --tail=100
kubectl -n lifelog logs lifelog-postgres-0 --tail=100
kubectl -n lifelog logs job/lifelog-migrate
kubectl -n lifelog describe deployment lifelog-frontend
kubectl -n lifelog describe deployment lifelog-backend
kubectl -n lifelog describe pod lifelog-postgres-0
kubectl -n lifelog describe pvc data-lifelog-postgres-0
kubectl -n lifelog describe job lifelog-migrate
kubectl -n lifelog describe service lifelog-frontend
kubectl -n lifelog describe ingress lifelog
kubectl -n lifelog get ciliumnetworkpolicy,networkpolicy
kubectl -n kube-system logs -l k8s-app=cilium --tail=100
~~~

- ImagePullBackOff: GHCR package 접근 권한, registry-credentials, 모든 노드의 HTTPS/DNS를 확인한다.
- PVC Pending: 기본 Class 존재, local PV의 용량/class/nodeAffinity 또는 CSI 상태를 확인한다.
- CreateContainerConfigError: Secret 이름/키를 확인한다. Secret 값을 출력하지 않는다.
- DB 인증 실패: 실제 DB 비밀번호와 Secret이 일치하는지 확인한다.
- UI 503: 앱 로그인 Secret과 backend readiness, UI 502: frontend→backend/DNS 정책을 확인한다.
- AI 503: OpenAI Secret 미설정, 502/504: 모델·키 권한·FQDN egress·공급자 상태를 확인한다.

업데이트는 DB 백업 → 새 image push/render → 이전 Job이 종료됐는지 확인 → 이전 Job만 삭제 →
새 migration 완료 → backend/frontend 적용·재시작 → HTTP 검증 순서다.

~~~bash
# 완료/실패 후 실행 중이 아닌 이전 Job에 대해서만
kubectl -n lifelog delete job lifelog-migrate
kubectl apply -f "$RELEASE_DIR/03-migration.yaml"
kubectl -n lifelog wait --for=condition=complete job/lifelog-migrate --timeout=300s
kubectl apply -f "$RELEASE_DIR/04-backend.yaml"
kubectl apply -f "$RELEASE_DIR/05-frontend.yaml"
# latest 또는 환경변수 변경 시 반드시 새 Pod로 반영
kubectl -n lifelog rollout restart deployment/lifelog-backend deployment/lifelog-frontend
kubectl -n lifelog rollout status deployment/lifelog-backend --timeout=300s
kubectl -n lifelog rollout status deployment/lifelog-frontend --timeout=300s
~~~

서로 다른 배포자가 동시에 migration을 실행하지 않는다. 비호환 migration은 계획 정지하고 수행한다.
rollout undo는 DB를 되돌리지 않으므로 기존 API와 현재 schema가 호환되는 경우에만 쓴다.
PostgreSQL은 단일 replica다. 3개 Kubernetes 노드가 있어도 DB HA를 제공하지 않는다.
PVC를 삭제하면 데이터가 손실될 수 있으며 local PV는 해당 VM 장애에 영향을 받는다.

~~~bash
umask 077
# 안전한 백업 디렉터리를 먼저 준비한다.
kubectl -n lifelog exec lifelog-postgres-0 -- sh -c \
  'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > /secure/backup/autolog.dump
~~~

정기 백업과 별도 DB 복원 검증을 수행한다. 앱 롤백에서 Namespace/PVC를 삭제하지 않는다.

## 개발·테스트

~~~bash
test -f .env || cp .env.example .env
# .env에 개발 DB 비밀번호, URL-encoded DATABASE_URL, APP_PASSWORD를 설정한다.
docker compose up --build -d --wait
curl --fail http://127.0.0.1:3000/health/ready
~~~

Compose 포트는 loopback에만 바인딩된다. 클라우드 프록시 빌드가 필요하면
python scripts/cloud-build.py 후 docker compose up -d --wait를 사용한다.
Compose 예제의 DB 비밀번호는 URL에 안전한 문자로 정한다. 기존 volume 비밀번호는 자동으로 바뀌지 않는다.

~~~bash
docker compose exec -T db sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' <<'SQL'
SELECT 'CREATE DATABASE lifelog_test'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'lifelog_test')
\gexec
SQL
cd backend
export UV_CACHE_DIR=/tmp/lifelog-uv-cache
test -x .venv/bin/python || uv venv .venv
uv pip sync --python .venv/bin/python --require-hashes requirements-dev.txt
# 전용 _test DB URL을 보안 환경변수로 주입한다.
TEST_DATABASE_URL="$YOUR_TEST_DATABASE_URL" .venv/bin/python -m pytest -q
~~~

~~~bash
cd frontend
npm ci
npm run typecheck
npm run build
# 로컬 Next 개발은 frontend/.env.example을 .env.local로 복사하고 로그인/백엔드 주소만 설정한다.
~~~

기존 checkout을 사용한다. 명시적 요청 없이 worktree를 만들지 않는다.
배포 YAML은 Kubernetes 1.34와 Cilium 1.18 CRD로 검증했다. 실제 cluster 버전의 admission은 단계별
server-side dry-run으로 다시 확인한다. 이 클라우드에는 Podman/대상 kubeconfig가 없으므로 실제 GHCR
push와 원격 rollout은 수행하지 않았다. OpenAI 호출 테스트는 mock을 사용했으며 실제 key로 외부 동작을 별도 확인한다.
