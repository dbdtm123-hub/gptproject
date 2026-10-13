# 공개 이미지 gateway (Garage private bucket)

쓰기 경로는 기존 Admin → Frontend → AutoLog Backend → Garage(RW key)다.
읽기 경로는 Reader → `https://img.myhomlab.win/uploads/...` → Cloudflare → image-gateway → Garage(R key)다.
기존 backend/frontend, Article DB, 인증과 autolog hostname 설정은 변경하지 않는다.
Garage Website access=false를 유지한다. 익명 bucket policy/PutBucketPolicy/GetBucketPolicy는 사용하지 않는다.

## 구현과 설정

독립 FastAPI/boto3 서비스다. Python 패키지와 hash lock은 image-gateway/에 별도로 둔다. 기존 앱/Node 의존성 변경은 없다.
공개 경로는 uploads/YYYY/MM/name.jpg|jpeg|png|webp|gif이며 UUID 업로드 key와 example.png를 지원한다.
GET은 GetObject, HEAD는 HeadObject만 호출한다. 다른 HTTP method는 405, prefix 밖은 404,
encoded path/query/역슬래시/traversal은 4xx로 거절한다. Cookie/Authorization/query로 S3 대상이나 인증을 바꿀 수 없다.
존재하지 않는 object는 404, Garage/읽기 실패는 상세 정보를 숨긴 JSON 502, reader 미설정은 503이다.
HEAD는 같은 status/header를 반환하고 body는 없다.

Content-Type/ETag를 유지하며 Cache-Control=public, max-age=31536000, immutable과 nosniff를 추가한다.
최대 10 MiB object를 응답 전 완전히 읽고 길이를 검사하므로 읽기 도중 실패도 JSON 502가 된다.
이미지 MIME만 허용하고 SDK body는 항상 닫는다. Uvicorn 동시 요청 제한 8, Pod 메모리 한도 512 MiB다.
이름이 같은 object를 덮어쓰지 않는 UUID 업로드와 함께 사용한다. 삭제된 이미지가 캐시에 남을 수 있다.
Draft에 삽입된 이미지도 URL을 알면 공개 조회할 수 있다. 이 gateway는 Article 권한을 검사하지 않는다.

| 환경변수 | 운영 값/출처 |
| --- | --- |
| GATEWAY_S3_ENDPOINT | http://garage.lifelog.svc.cluster.local:3900 |
| GATEWAY_S3_BUCKET | autolog-public (고정) |
| GATEWAY_S3_REGION | garage (실제 Garage s3_region과 일치) |
| GATEWAY_S3_ACCESS_KEY | lifelog-garage-reader Secret: S3_ACCESS_KEY |
| GATEWAY_S3_SECRET_KEY | lifelog-garage-reader Secret: S3_SECRET_KEY |

전용 GATEWAY_ 변수만 사용하며 .env/AWS 기본 credential chain/backend S3_* 변수로 fallback하지 않는다.
Pod는 Secret reader만 받는다. read/write 분리는 실제 Garage key permission으로도 강제해야 한다.
내부 health/live, health/ready는 probes용이다. readiness는 설정 유무를 확인하며 Garage 연결은 실제 GET으로 확인한다.
Cloudflare 예시의 hostname catch-all 404로 health 및 다른 경로의 외부 접근을 막는다.

## 적용 순서

저장소 루트에서, 운영 kubectl/Podman 관리 호스트로 진행한다. 이번 변경은 자동 배포하지 않는다.

1. Garage에 **새 autolog-reader key**를 생성하고 autolog-public에 read만 부여한다.
   autolog-backend key를 재사용하지 않는다. 기존 bucket/layout/Website 설정을 변경하지 않는다.
   설치된 Garage 버전의 `garage key --help`, `garage bucket allow --help`를 먼저 확인한다.
   CLI 기본 형태는 아래와 같다. 생성 결과에 secret이 있으므로 보호된 운영 터미널에서만 실행한다.

```bash
# 실제 Garage 실행 방식에 맞춰 exec 경로를 선택한다.
kubectl -n lifelog exec deployment/garage -- /garage key create autolog-reader
kubectl -n lifelog exec deployment/garage -- /garage bucket allow --read autolog-public --key autolog-reader
# key info로 read=true / write=false / owner=false를 운영 터미널에서 확인한다.
# Secret 파일은 Git 밖의 보호된 SECRET_DIR에 각각 0600 권한으로 준비한다.
kubectl -n lifelog create secret generic lifelog-garage-reader \
  --from-file=S3_ACCESS_KEY="$SECRET_DIR/S3_ACCESS_KEY" \
  --from-file=S3_SECRET_KEY="$SECRET_DIR/S3_SECRET_KEY"
```

Garage Pod가 StatefulSet이면 exec deployment 대신 실제 Pod/StatefulSet 이름을 사용한다.
Key 생성 명령이 secret을 출력하므로 출력/Secret YAML을 Git, CI 로그, 채팅에 남기지 않는다.
Secret 구조는 k8s/examples/garage-reader-secret.yaml.example에 있고 실제 값은 넣지 않는다.
기존 default-deny 환경의 Garage backend/RPC 및 cloudflared 외부/DNS 허용 정책을 유지한다.
추가 NetworkPolicy는 기존 정책과 합쳐진다. Cilium explicit deny가 있으면 별도 점검한다.

2. gateway만 build/push한다. 기존 backend/frontend image는 그대로 둔다.

```bash
GATEWAY_IMAGE=ghcr.io/dbdtm123-hub/autolog-image-gateway:reader-v1
podman build -f image-gateway/Dockerfile -t "$GATEWAY_IMAGE" image-gateway
podman login ghcr.io
podman push "$GATEWAY_IMAGE"
```

GHCR private image라면 기존 registry pull Secret을 gateway Deployment의 imagePullSecrets에 추가한다.
클러스터 런타임은 containerd다. 노드에 Docker daemon이 필요하지 않다.

3. release overlay에 gateway image만 지정하고 apply한다. 원본 placeholder manifest 전체 앱 apply는 하지 않는다.

```bash
GATEWAY_RELEASE=$(mktemp -d)
cp -R k8s/image-gateway "$GATEWAY_RELEASE/base"
python3 - "$GATEWAY_RELEASE" "$GATEWAY_IMAGE" <<'PY'
import json, pathlib, sys
root, image = pathlib.Path(sys.argv[1]), sys.argv[2]
name, tag = image.rsplit(':', 1)
(root / 'kustomization.yaml').write_text(json.dumps({
 'apiVersion':'kustomize.config.k8s.io/v1beta1','kind':'Kustomization',
 'resources':['base'], 'images':[{'name':'autolog/image-gateway','newName':name,'newTag':tag}]
}))
PY
kubectl kustomize "$GATEWAY_RELEASE" > "$GATEWAY_RELEASE/rendered.yaml"
# rendered.yaml을 검토: image-gateway 자원만 포함, 실제 image tag, reader Secret 참조.
kubectl apply --dry-run=server -f "$GATEWAY_RELEASE/rendered.yaml"
kubectl apply -f "$GATEWAY_RELEASE/rendered.yaml"
kubectl -n lifelog rollout status deployment/image-gateway --timeout=300s
kubectl -n lifelog get pods -l app=image-gateway
```

Namespace lifelog는 기존 것을 사용한다. 새 ConfigMap/ServiceAccount/NetworkPolicy/Deployment/ClusterIP Service만 적용한다.
PodSecurity restricted: non-root UID10001, RuntimeDefault seccomp, escalation 금지, ALL capabilities 제거, read-only root filesystem.
통신은 lifelog app=cloudflared → app=image-gateway TCP8080, gateway → app=garage TCP3900와 DNS53만 추가한다.
frontend/backend/관리포트로의 통신 허용은 추가하지 않는다. 기존 광범위 allow 정책이 있으면 실제 유효 권한도 검토한다.

4. Cloudflare 변경 전에 내부 실제 object 조회를 확인한다.

```bash
kubectl -n lifelog port-forward service/image-gateway 18080:8080
# 다른 터미널에서 이미 업로드된 실제 key 사용:
IMAGE_KEY=uploads/YYYY/MM/ACTUAL_UUID.png
curl --fail -D /tmp/image-headers -o /tmp/image-check.png "http://127.0.0.1:18080/$IMAGE_KEY"
curl --fail -I "http://127.0.0.1:18080/$IMAGE_KEY"
curl -i -X POST "http://127.0.0.1:18080/$IMAGE_KEY" # 405
```

5. 기존 cloudflared ConfigMap의 ingress에 아래 두 규칙만 추가한다.
   k8s/examples/cloudflared-image-ingress.yaml.example는 **fragment**이며 전체 ConfigMap으로 apply하지 않는다.
   기존 autolog rule/tunnel ID/credential/token/mount는 그대로 유지한다. 글로벌 fallback http_status:404 앞에 넣는다.

```yaml
- hostname: img.myhomlab.win
  path: ^/uploads/.*
  service: http://image-gateway.lifelog.svc.cluster.local:8080
- hostname: img.myhomlab.win
  service: http_status:404
```

ConfigMap 수정 전 운영 저장소 밖에 0600 백업을 둔다. 기존 설정 파일/CLI args 기준으로
`cloudflared tunnel ingress validate`, `cloudflared tunnel ingress rule https://img.myhomlab.win/uploads/2026/10/example.png`를 실행한다.
기존 cloudflared Deployment를 rollout restart하고 status 확인한다. 다른 tunnel을 생성하거나 credential을 교체하지 않는다.
Cloudflare DNS에 img.myhomlab.win을 **기존 tunnel**로 연결한다. 관리자 Access 규칙은 autolog 관리자 화면에 유지하며
image hostname에는 로그인 강제를 적용하지 않는다. Tunnel 연결 대상은 Garage endpoint가 아니라 image-gateway Service다.

```bash
curl --fail -I "https://img.myhomlab.win/$IMAGE_KEY"
curl --fail -o /tmp/public-image.png "https://img.myhomlab.win/$IMAGE_KEY"
curl -i -X POST "https://img.myhomlab.win/$IMAGE_KEY" # 405
curl -i https://img.myhomlab.win/health/ready # Cloudflare 404
curl --fail -I https://autolog.myhomlab.win/
```

## 테스트와 장애 확인

```bash
cd image-gateway
uv venv .venv
uv pip sync --python .venv/bin/python --require-hashes requirements-dev.txt
.venv/bin/python -m pytest -q
# 운영:
kubectl -n lifelog logs deployment/image-gateway --all-containers --tail=100
kubectl -n lifelog describe deployment image-gateway
kubectl -n lifelog describe pods -l app=image-gateway
kubectl -n lifelog get networkpolicy
kubectl -n lifelog logs -l app=cloudflared --tail=100
```

테스트는 mock S3로 GET/HEAD/404/405/경로 제한/SDK 장애/전용 credential/불완전 body를 검증한다.
실제 reader 권한, Garage 연결, cluster admission, DNS/Tunnel은 위 운영 명령으로 검증한다.
503이면 Secret 주입/region/config 확인, 502이면 Garage read 권한/서비스/DNS/NetworkPolicy 확인,
404이면 Markdown key와 실제 bucket object가 일치하는지 확인한다. Secret value나 raw SDK 오류는 공개 응답에 없다.

## 롤백

새 gateway 버전 문제라면 `kubectl -n lifelog rollout undo deployment/image-gateway` 후 status를 확인한다.
최초 도입 전체 롤백은 먼저 img hostname에 추가한 두 ingress 규칙만 제거하고 기존 cloudflared를 재시작한다.
그 다음 gateway release rendered.yaml로 `kubectl delete -f "$GATEWAY_RELEASE/rendered.yaml"`한다.
새 reader key/Secret은 참조가 없음을 확인한 뒤 운영자가 회수한다.
기존 autolog 규칙, backend/frontend, writer key, Garage bucket/objects/Website=false, DB/PVC/NFS는 유지한다.
Gateway를 제거하면 이미 저장된 Markdown 이미지 URL은 조회되지 않으므로 계획해서 실행한다.
