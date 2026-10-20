# Authelia / AutoLog MCP 운영 배포 사전 검토

2026-10-20, 현재 `work` 작업 트리 기준. **실제 적용하지 않았다.** 기존 미커밋 OAuth 변경을 보존했다.
통합 기준은 GitHub work `db9bd91b8b50e94ac57e02d17ac1490f11d84eac`다. 원격의 OAuth 보안·Kustomize 후속 수정을 유지한다.
기존 상세 보안 검증은 [홈랩 인증 설계](mcp-auth-homelab.md), [검증 기록](mcp-auth-validation.md)을 함께 참조한다.
이 문서의 확정 운영 조건이 예전 문서의 신규 PVC/StorageClass 가정보다 우선한다.

| 항목 | 확정값 |
| --- | --- |
| issuer / 인증 도메인 | `https://auth.myhomlab.win` |
| MCP resource / audience | `https://mcp.myhomlab.win/mcp` |
| Namespace | `lifelog` |
| StorageClass | 기존 `authelia-local` |
| PV / PVC | 기존 `authelia-auth-pv` / `autolog-auth-data` |
| local PV 노드 | `k8s-worker01` |
| Authelia | `4.39.28` + 검토한 registry digest |

Cinder를 사용하지 않는다. PV/PVC/StorageClass는 생성·변경·삭제 대상에서 제외한다.
`k8s/auth`의 PVC 리소스를 제거했고 Deployment는 기존 claim만 참조한다.
노드 선택은 `kubernetes.io/hostname: k8s-worker01`이다. 실제 노드 label과 PV nodeAffinity가 일치해야 한다.

## 1. 적용 전 필요한 값 및 중단 조건

다음 값은 아직 운영 증거가 없으므로 임의로 채우지 않는다.

- 관리 호스트의 정확한 Kubernetes context와 권한.
- ChatGPT 연결 화면이 제시하는 **정확한** HTTPS callback. 검증용 callback을 쓰면 추가 URI를 검토하고 시험 후 제거한다.
- 관리자 username/email. 기본 예시는 `owner`. Authelia에는 별도 관리자 가입 UI/관리자 권한 계정이 없으며 file backend의 사용자와 owner OIDC 정책으로 관리한다.
- Authelia 4.39.28의 실제 아키텍처별 검증 digest, MCP 코드가 포함된 CI 성공 backend image digest 및 필요한 기존 imagePullSecret.
- 현재 사용 중인 blog/image 공개 base URL. 기존 AutoLog·Garage·Sites URL을 그대로 사용한다.
- 최초 OAuth 로그인 후 확인한 사용자 `sub` 및 명시적 `autolog:read`/`autolog:write` 권한.
- Tunnel 관리 방식, cloudflared namespace/label, Cilium 및 DNS label, 기존 allow/deny 정책. renderer는 Cilium CRD를 요구한다.
- 기존 PVC 상태/용량/바인딩/기존 데이터와 key 소유 여부, local 디렉터리 UID/GID 10001 쓰기 가능 여부, 백업 위치.

기존 SQLite가 이미 있으면 새 storage/key 세트를 생성해 덮어쓰지 않는다. 기존 DB와 일치하는 암호화/서명/사용자 Secret 및 백업을 확보하고 migration 영향을 먼저 검토한다.
아래 Secret 생성 절차는 기존 데이터와 Secret이 없는 최초 설치에만 해당한다.

```bash
export AUTOLOG_CONTEXT=실제_검토한_CONTEXT
bash scripts/oauth/read_only_preflight.sh
kubectl --context "$AUTOLOG_CONTEXT" get sc authelia-local -o yaml
kubectl --context "$AUTOLOG_CONTEXT" get pv authelia-auth-pv -o yaml
kubectl --context "$AUTOLOG_CONTEXT" -n lifelog get pvc autolog-auth-data -o yaml
kubectl --context "$AUTOLOG_CONTEXT" get node k8s-worker01 --show-labels
```

PVC가 Bound이며 `spec.volumeName=authelia-auth-pv`, `storageClassName=authelia-local`인지 확인한다.
PV claimRef의 namespace/name/UID가 해당 PVC와 일치하고 local.path와 nodeAffinity가 올바른지 확인한다.
PVC가 Pending이거나 잘못 바인딩됐으면 중단한다. 재생성·강제 바인딩·reclaimPolicy 변경으로 해결하지 않는다.
기존 host path 권한 조정이 필요해도 먼저 소유 데이터와 변경 영향을 확인한다. Pod fsGroup만으로 local 디렉터리 권한이 해결된다고 가정하지 않는다.

## 2. 비밀 없는 렌더링과 검증

관리 호스트에 kubectl/Kustomize, Python, OpenSSL, 고정 이미지 검증용 Docker를 준비한다.
`.deploy/oauth-settings.json`은 아래 **입력 형식**으로 작성한다. placeholder가 남으면 renderer가 거부한다.

```json
{
  "issuer": "https://auth.myhomlab.win",
  "resource": "https://mcp.myhomlab.win/mcp",
  "redirect_uri": "https://REPLACE.invalid/callback",
  "username": "owner",
  "auth_storage_class": "authelia-local",
  "auth_image": "ghcr.io/authelia/authelia@sha256:REPLACE"
}
```

실제 callback/digest를 입력하면 sub 없이 Auth만 렌더링할 수 있다. `--stage auth`는 MCP를 출력하지 않는다.

```bash
python3 scripts/oauth/render_release.py --stage auth \
  --settings .deploy/oauth-settings.json --output .deploy/oauth-auth-01
```

렌더링 결과를 검토한다: namespace `lifelog`, image digest, owner `two_factor`, 기존 claim 참조, 노드 선택,
Secret 리소스·PV·PVC·StorageClass 부재. template의 issuer/cookie/audience/callback이 운영값인지 확인한다.
`k8s/auth` 또는 `k8s/mcp` base를 직접 apply하지 않는다. 예제 주소와 mutable MCP image가 남아 있다.

고정 이미지의 `authelia --help` / `validate-config --help`와 template flag를 확인하고,
실제 mount와 같은 config/Secret 경로를 격리된 컨테이너에 제공하여 `validate-config`를 실행한다.
검증 과정의 config/template 출력에 Secret이 포함될 수 있으므로 원문을 CI/채팅에 남기지 않는다.
먼저 `scripts/oauth/authelia_lab.py`의 고정 버전 양성/음성 검증을 운영 credential 없이 통과시킨다.

```bash
backend/.venv/bin/python scripts/oauth/authelia_lab.py
kubectl --context "$AUTOLOG_CONTEXT" -n lifelog apply --dry-run=server \
  -f .deploy/oauth-auth-01/auth.yaml
```

server dry-run은 admission 검증이며 Pod 기동/볼륨 쓰기/OAuth 호환성의 증거가 아니다.
기존 cloudflared가 egress 비격리 상태였다면 새 NetworkPolicy가 기존 Tunnel 연결을 차단할 수 있다.
기존 DNS/Cloudflare egress 허용을 먼저 확인하고 정책을 검토한 뒤 적용한다. 현재 정책을 통째로 교체하지 않는다.

## 3. 최초 사용자와 운영 Secret 준비

비공개 관리 호스트에서 shell tracing과 터미널 녹화를 끄고 실행한다. 운영 Secret은 GitHub/Actions/issue/채팅에 저장하지 않는다.
`.secrets`는 Git 제외지만 별도의 암호화 백업도 필요하다. 다음 명령은 **나중에 실행할 절차**다.

```bash
umask 077
backend/.venv/bin/python scripts/oauth/prepare_secrets.py \
  --directory .secrets/autolog-auth-new --username owner --email 실제_이메일
```

사용자 암호는 숨김 입력이며 users.yml에 hash로만 기록된다. username을 바꾸면 설정의 owner 정책도 함께 맞춘다.
`autolog-auth`, `autolog-mcp-oauth`가 이미 존재하면 중단하고 기존 설치를 조사한다. 새 설치에는 다음처럼 create를 사용한다.

```bash
kubectl --context "$AUTOLOG_CONTEXT" -n lifelog create secret generic autolog-auth \
  --from-file=session=.secrets/autolog-auth-new/session \
  --from-file=storage=.secrets/autolog-auth-new/storage \
  --from-file=reset=.secrets/autolog-auth-new/reset \
  --from-file=hmac=.secrets/autolog-auth-new/hmac \
  --from-file=oidc.pem=.secrets/autolog-auth-new/oidc.pem \
  --from-file=chatgpt.hash=.secrets/autolog-auth-new/chatgpt.hash \
  --from-file=introspection.hash=.secrets/autolog-auth-new/introspection.hash \
  --from-file=users.yml=.secrets/autolog-auth-new/users.yml
kubectl --context "$AUTOLOG_CONTEXT" -n lifelog create secret generic autolog-mcp-oauth \
  --from-file=INTROSPECTION_CLIENT_SECRET=.secrets/autolog-auth-new/introspection.secret
```

디렉터리 전체를 Secret으로 만들지 않는다. `chatgpt.secret`은 ChatGPT client 입력용이며 Auth Pod에 넣지 않는다.
기존 `lifelog-api`, PostgreSQL/Garage/Tunnel Secret은 변경하지 않는다.

## 4. Auth 단독 기동 및 MFA 등록

적용 전 검토가 끝난 뒤 다음 단계만 실행한다.

```bash
kubectl --context "$AUTOLOG_CONTEXT" -n lifelog apply -f .deploy/oauth-auth-01/auth.yaml
kubectl --context "$AUTOLOG_CONTEXT" -n lifelog rollout status deployment/autolog-auth --timeout=180s
kubectl --context "$AUTOLOG_CONTEXT" -n lifelog get pods -l app=autolog-auth -o wide
```

Pod가 k8s-worker01에서 실행되고 기존 claim을 mount했는지 확인한다. 최초 기동은 기존 PVC에 SQLite/알림 파일을 쓴다.
CrashLoop/Pending이면 Secret/key·권한·이미지·설정을 조사하고 중단한다. PVC 삭제나 보안 검증 완화로 우회하지 않는다.

Auth 전용 tunnel-fragment를 기존 catch-all 앞에 병합한다. 기존 Sites/AutoLog/Garage route와 Access 정책을 보존한다.
`https://auth.myhomlab.win`의 TLS/discovery/forwarded header 정규화를 확인한다. 잘못된 forwarded host로 issuer가 변하면 공개를 중단한다.
OIDC endpoint에 추가 Access 로그인/Browser Challenge를 넣지 않는다.
브라우저에서 owner 암호로 로그인하고 MFA 등록을 시작한다. 보호된 `/data/notifications.txt`의 일회성 링크는
관리자 전용 세션에서만 확인한다. 파일을 로그/공개 백업/채팅에 복사하지 않는다.
TOTP 또는 WebAuthn 등록 후 로그아웃/2차 인증 재로그인을 확인한다. OIDC owner 정책의 `two_factor`를 유지한다.

## 5. 실제 sub 확정 후 MCP 준비

정확히 등록된 callback으로 `autolog-chatgpt`의 실제 authorize 흐름을 수행한다.
`resource=https://mcp.myhomlab.win/mcp`, code+PKCE S256, state, owner MFA/동의가 필요하다.
처음 OAuth 사용자 식별자가 만들어진 뒤 다음 도움말을 **동일 4.39.28 Pod**에서 확인한다.

```bash
kubectl --context "$AUTOLOG_CONTEXT" -n lifelog exec deployment/autolog-auth -- \
  authelia storage user identifiers export --help
```

해당 버전의 config/template flag와 export 옵션으로 식별자를 비공개 파일에 export한다.
owner와 실제 client sector에 대응하는 UUID를 확인하고, 실제 code를 S256으로 교환한 **access token**의
별도 resource-server Basic introspection JSON `sub`와 대조한다. username/email/ID token을 대용하지 않는다.
토큰 응답이나 export 원문은 보고서에 넣지 않는다. 최신 CLI 참고 문서보다 고정 이미지 도움말을 우선한다.
[공식 식별자 export 참고](https://www.authelia.com/reference/cli/authelia/authelia_storage_user_identifiers_export/).

설정 JSON에 다음 값을 추가한다: `backend_image`(검증 digest), `blog_public_base`, `image_public_base`(현재 URL),
`subject_scopes`(실제 sub → `["autolog:read", "autolog:write"]`). sub를 추측해 미리 넣지 않는다.

```bash
python3 scripts/oauth/render_release.py --stage mcp --settings .deploy/oauth-settings.json \
  --output .deploy/oauth-release-01
kubectl --context "$AUTOLOG_CONTEXT" -n lifelog apply --dry-run=server \
  -f .deploy/oauth-release-01/mcp.yaml
```

MCP 단독 출력에는 auth.yaml이 없다. callback 변경이 필요하면 Auth를 `--stage auth`로 별도 렌더링·검증/갱신한다.
MCP는 `lifelog-api/API_TOKEN` 참조를 유지하며 DB/S3 credential을 받지 않는다. 이미지 pull 및 FQDN 정책을 확인한 뒤 MCP만 적용한다.

```bash
kubectl --context "$AUTOLOG_CONTEXT" -n lifelog apply -f .deploy/oauth-release-01/mcp.yaml
kubectl --context "$AUTOLOG_CONTEXT" -n lifelog rollout status deployment/autolog-mcp --timeout=180s
```

기존 Tunnel에 MCP 두 경로(`/mcp`, protected-resource metadata)와 해당 hostname의 404 fallback만 병합한다.
다른 hostname의 catch-all 순서는 보존한다. MCP health endpoint를 공개하지 않는다.

## 6. 운영 연결 합격 기준

- 실제 HTTPS discovery의 issuer/endpoints와 S256 확인. 미인증 `/mcp`는 401 및 올바른 resource metadata challenge.
- 실제 MFA/code 교환/introspection에서 active, exact iss/aud, 유효 exp, 실제 sub, 필요한 scope, Bearer access token 확인.
- 잘못된 issuer/audience/sub, 만료, refresh token 사용, 부족 scope, 잘못된 PKCE 및 code 재사용 거부. 실패하면 배포 중단.
- `verify_connection.py --begin/--finish`를 운영 issuer/resource와 정확히 등록된 검증 callback으로 실행하고 refresh도 확인.
  상세 명령은 홈랩 문서 참조. 검증용 callback은 완료 후 제거한다.
- 실제 ChatGPT에서 새 AutoLog MCP 연결을 OAuth로 추가. endpoint는 `https://mcp.myhomlab.win/mcp`, client ID는
  `autolog-chatgpt`, 비밀은 비공개 파일에서 UI에 직접 입력. 기존 Sites 연결은 수정/삭제하지 않는다.
- 실제 연결에서 tools/list 및 check_connection, 지침, 목록/검색을 확인한다. 기존 글을 수정하지 않는다.
  쓰기 검증은 별도 지정한 테스트 초안에만 수행한다. 업로드/private reads는 기본 off를 유지한다.

## 영향과 복구

추가 request는 Auth 50m/128Mi + MCP 100m/128Mi, limit 합계 CPU 2 core/메모리 1Gi다.
Auth Recreate/단일 local PV는 재시작·노드 장애 시 인증 중단을 의미한다. 브라우저 메모리 세션도 재로그인이 필요할 수 있다.
Auth 장애 때 MCP는 fail closed한다. 기존 backend/frontend/PostgreSQL/Garage/image-gateway/Sites 배포와 데이터는 변경하지 않는다.
다만 같은 Tunnel의 route/NetworkPolicy를 잘못 변경하면 기존 서비스도 영향을 받으므로 변경 전후 기존 URL을 확인한다.

최초 변경 전 기존 SQLite 여부와 일관된 백업을 확인하고, 이후 DB와 users/암호화/서명 키를 함께 암호화 백업한다.
실행 중 DB 파일 단순 복사 대신 SQLite online backup 또는 Auth 단독 중지 후 일관된 DB/WAL/SHM 백업을 사용한다.
복구는 새 Auth/MCP route만 비활성화하고 필요하면 해당 Deployment만 scale 0 한다.
PV/PVC/StorageClass/Secret은 삭제하지 않는다. `kubectl delete -k`, prune 및 전체 앱 재배포를 복구 절차로 사용하지 않는다.
SQLite migration 후에는 image undo만으로 복구된다고 가정하지 않는다. 일관된 DB/key 백업과 호환 이미지로 복구한다.

## 통합 검증 범위

통합 기준은 GitHub work db9bd91이다. 최신 OAuth 검증·안전한 Kustomize 복사 작업공간 처리를 유지했다.
`auth_storage_class=authelia-local`은 필수 입력 검증에만 사용하며 PVC patch는 생성하지 않는다.
`--stage auth`는 sub/backend 이미지 없이 준비할 수 있고, `--stage mcp`는 실제 확인한 sub와 backend digest가 필요하다.
두 단계 모두 정확한 HTTPS callback과 인증 설정이 누락되거나 잘못되면 실패한다. 기본 `--stage all`도 유지한다.

로컬 회귀 검증: OAuth acceptance + MCP + image validation + upload guard **119 passed**.
Starlette/httpx deprecation warning 1건. YAML 구조·PV/PVC/StorageClass/Secret 리소스 제외 및 diff whitespace 검사 통과.
로컬에 kubectl이 없어 실제 Kustomize 검증은 GitHub CI의 manifests job에서 수행한다.
`scripts/oauth/check_release_render.py`는 합성 digest/callback/sub로 실제 auth/mcp/all 출력을 검사한다.
검사항목은 단계별 리소스 분리, namespace, 기존 claim과 node 참조, digest, OAuth callback/정책 및 Cilium FQDN이다.
합성 값 렌더링은 운영 digest·callback·sub가 검증됐다는 뜻이 아니다.

운영 kubeconfig를 사용하지 않았다. Kubernetes admission dry-run, 실제 PV/PVC 바인딩·디렉터리 권한,
고정 이미지 config validation/기동, DNS/TLS, MFA/OAuth 및 ChatGPT 연결은 운영 관리 호스트에서 후속 검증해야 한다.
이 작업에서 클러스터 적용·운영 Secret 생성/변경·기존 서비스 변경은 수행하지 않는다.
