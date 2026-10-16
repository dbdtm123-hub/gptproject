# AutoLog CI/CD: 검증된 이미지 + Tailscale 승인형 Kubernetes 배포

> **현재 범위:** `work` 브랜치용 GitHub Actions CI, SHA-tagged GHCR 이미지, 승인형 CD 워크플로 **코드**와 Kubernetes 최소 RBAC 템플릿. 운영 클러스터에는 이 문서만으로 어떤 변경도 자동 적용하지 않는다. 외부 GitHub 환경 설정/크레덴셜/Tailscale 경로는 별도 설정이 필요하다.

## 왜 이런 구조인가?

이 저장소는 **public**이다. 공개 저장소가 PR 코드를 실행할 수 있는 **상시 self-hosted runner**에 운영 Kubernetes 자격증명을 제공하면 위험하다. 따라서 **GitHub-hosted 일회용 러너**에 Tailscale 연결을 잠깐 생성하는 방식으로 변경했다. Github-hosted 러너에 올라가는 유일한 클러스터 인증은 **production Environment 승인 후에만** 제공되는 최소 권한 kubeconfig이다.

```text
개발 VM(Codex) ── 별도 개발 브랜치 ── PR → work
                                        │
                          CI: pytest · tsc · Next build
                                        │
                        GHCR: SHA-tagged 이미지 3개
                                        │
                         [수동 release SHA 지정]
                                        │
                   [GitHub production 승인/보호]
                                        │
                  GitHub-hosted 임시 Ubuntu 러너
                                        │
                      Tailscale: tag:autolog-cd
                                        │
                Kubernetes API (Tailscale ACL 제한)
                                        │
                lifelog의 앱 Deployment만 패치
```

운영 PostgreSQL, Garage, PVC, Namespace, Secrets, migration, Cilium 설정은 변경하지 않는다.

## CI: 자동 동작

- `work` 대상 PR → Backend(Python 3.12, 테스트용 PostgreSQL), image-gateway(Python 3.12), Frontend(Node 22/Next.js), 오프라인 Kustomize 검사. **PR에서 이미지 업로드 안 함**.
- `work` push → 위 검증이 모두 통과하면 GHCR에 세 이미지를 발행.
- `workflow_dispatch` CI 수동 검사 → 이미지 업로드하지 않음.
- 이미지 경로(이전에 수동 업로드한 권한 미연결 `autolog/backend`·`autolog/frontend`와 충돌하지 않게 변경):

```text
ghcr.io/dbdtm123-hub/gptproject-autolog-ci/backend:sha-<40자리_SHA>
ghcr.io/dbdtm123-hub/gptproject-autolog-ci/frontend:sha-<40자리_SHA>
ghcr.io/dbdtm123-hub/gptproject-autolog-ci/image-gateway:sha-<40자리_SHA>
```

**주의:** 이미지 태그는 digest가 아니며 불변성은 레지스트리 정책에 따라 다르다. 운영에는 성공한 동일 work SHA의 이미지 세 개만 사용한다. 첫 발행 후 패키지가 기본 비공개일 수 있으므로 **클러스터에서 이미지 Pull 가능한지** 반드시 확인한다. Private GHCR 패키지는 노드용 최소 읽기 권한 imagePullSecret이 필요할 수 있다.

## CD 선행 설정: 코드는 준비됐지만 운영 접근은 아직 불가

### 1. GitHub production Environment

GitHub 저장소 Settings → Environments → `production` 환경을 **미리 생성하고 필수 승인자(required reviewers)**를 설정한다. 사용 중인 계정/플랜에서 보호 규칙이 지원되는지 UI에서 확인한다. Repository의 Actions 권한과 브랜치 보호도 설정하고, 최소한 `work`의 임의 force-push와 검증되지 않은 직접 Push를 금지한다.

**환경 보호/승인 규칙을 설정하지 않은 상태에서 배포용 비밀값부터 넣으면 안 된다.**

다음 세 값을 **Repository-wide Secrets가 아니라 production Environment Secrets**로 등록한다. 채팅, 코드, Actions 로그에 값을 표시하지 않는다.

| Environment Secret | 뜻 |
| --- | --- |
| `TS_OAUTH_CLIENT_ID` | Tailscale OAuth 클라이언트 ID |
| `TS_OAUTH_SECRET` | Tailscale OAuth Secret (`auth_keys` scope + `tag:autolog-cd` 허용) |
| `AUTOLOG_KUBECONFIG_B64` | 아래 `autolog-ci-deployer` 최소 권한 kubeconfig를 base64 인코딩한 값 |

Tailscale의 Admin → Trust credentials/OAuth clients에서 위 계정과 `tag:autolog-cd`를 설정하고 Tailnet ACL/grants는 **해당 태그에서 Kubernetes API(대개 TCP 6443)로만** 접근하도록 제한한다. 모든 홈랩 노드와 SSH/DB/관리 UI에 광범위한 접근을 주지 않는다.

### 2. Kubernetes 최소 RBAC (사람이 클러스터에서 수동 적용)

`k8s/examples/autolog-ci-deployer-rbac.yaml.example` 파일은 **자동 배포/검증용 Kustomization에 포함하지 않았다.** 관리자 kubeconfig를 가진 별도 관리 호스트에서 먼저 내용을 검토한다.

```bash
kubectl --context <ADMIN_CONTEXT> -n lifelog get deployments
kubectl --context <ADMIN_CONTEXT> apply -f k8s/examples/autolog-ci-deployer-rbac.yaml.example
```

이 예시는 `lifelog` 네임스페이스에서 세 Deployment에만 update/patch, Deployment/ReplicaSet/Pod에 읽기 권한을 부여한다. Secret 읽기, DB 접근, cluster-admin 권한은 제공하지 않는다.

RBAC 검증:

```bash
kubectl -n lifelog auth can-i patch deployment/lifelog-backend --as=system:serviceaccount:lifelog:autolog-ci-deployer
kubectl -n lifelog auth can-i patch deployment/lifelog-frontend --as=system:serviceaccount:lifelog:autolog-ci-deployer
kubectl -n lifelog auth can-i get secrets --as=system:serviceaccount:lifelog:autolog-ci-deployer
# 첫 두 개는 yes, 마지막은 no가 기대값
```

### 3. 제한된 kubeconfig 준비

`autolog-ci-deployer` ServiceAccount에 대한 토큰과 클러스터 서버 주소, TLS 인증서 정보를 조합하여 **별도의 제한된 kubeconfig**를 만든다. admin 사용자 자격증명을 GitHub로 옮기지 않는다.

현대 Kubernetes에서 `kubectl -n lifelog create token autolog-ci-deployer --duration=24h` 등으로 발급하는 토큰은 **만료된다**. 짧은 수명의 토큰은 배포 시마다 교체하거나, 추후 별도 workload identity/token broker를 도입해야 한다. 장기 비밀 토큰을 무기한 방치하지 않는다.

이 kubeconfig의 API server 주소는 **Tailscale에서 실제 도달 가능한 주소**여야 하고 서버 TLS 인증서 이름과도 일치해야 한다. 현재 홈랩에서 어떤 노드 IP·서브넷 라우트가 이를 만족하는지는 아직 검증하지 않았다. TLS 검증을 비활성화하지 않는다.

로컬에 저장한 *제한된* kubeconfig를 안전한 PC에서 base64로 바꿔 `AUTOLOG_KUBECONFIG_B64`에 등록할 수 있다. **이 내용은 비밀번호/토큰과 같으므로 공개하거나 Git에 커밋하면 안 된다.**

```bash
# 보안상 출력 내용을 채팅·로그에 보내지 말 것
base64 -w0 /secure/path/autolog-deployer.kubeconfig
```

### 4. GHCR 이미지 읽기 권한

기존 Pod가 GHCR에 로그인된 상태인지, 새 `gptproject-autolog-ci/*` 패키지를 Kubernetes 노드가 받아올 수 있는지 확인한다. 필요하면 package visibility를 공개로 전환하거나 `lifelog`에 GHCR 읽기 전용 imagePullSecret을 별도 운영 절차로 연결한다. **이 PR은 Secret 변경을 포함하지 않는다.**

## 수동 CD 실행

`.github/workflows/autolog-deploy.yml`은 **자동 Push 배포가 아니다**.

GitHub에서 `workflow_dispatch` UI를 이용하려면 **워크플로 파일이 GitHub 기본 브랜치에도 있어야 한다.** 현재 `main`이 기본 브랜치이고 `work`는 개발 브랜치이므로, 기본 브랜치에 **동일한 수동 CD 워크플로**를 반영하거나 GitHub 기본 브랜치 정책을 바꿔야 한다. 이 변경은 별도 검토 없이 하지 않는다.

실행 시 입력:
- `release_sha`: `work` 브랜치에서 CI와 이미지 Publish가 성공한 40자리 커밋 SHA
- `confirmation`: 정확히 `DEPLOY-LIFELOG`

워크플로는 이 SHA가 `work`에 포함되는지, **해당 SHA의 CI 워크플로가 성공했는지**, GHCR 이미지가 모두 존재하는지 확인한 다음, Tailscale로 연결해 배포한다.

`scripts/deploy-autolog.sh`는 기본값이 **계획 모드**다.

```bash
# 최소 RBAC kubeconfig를 가진 별도 관리 호스트에서만 테스트
export KUBECONFIG=/secure/path/autolog-deployer.kubeconfig
bash scripts/deploy-autolog.sh <40자리_SHA>
```

실제 apply에는 `--apply`와 `AUTOLOG_CONFIRM_DEPLOY=DEPLOY-LIFELOG`가 둘 다 있어야 한다. 자동 CD는 해당 확인 문자열과 production 환경 승인 뒤에만 실행한다. 서비스 롤아웃 중 오류가 나면 변경한 **Deployment의 `rollout undo`를 시도**하나, DB를 되돌리지는 않는다.

## 중요한 점검 / 완료 기준

1. PR의 Python/Frontend/Kustomize 검사가 녹색인가?
2. `work` Push 후 세 GHCR 패키지 발행이 성공했는가?
3. production 승인자를 활성화했는가?
4. GitHub-hosted runner의 `tag:autolog-cd`가 Kubernetes API에 **최소 범위로** 연결되는가?
5. 최소권한 ServiceAccount로 Deployment 정보 조회와 rollout이 가능한가?
6. PostgreSQL 백업·복원 계획, 스키마 변경 여부 확인을 마쳤는가?
7. 배포 후 공개 AutoLog, 이미지 GET, 글 작성, 읽기·쓰기 연동을 검증했는가?

**상태:** 본 문서는 코드/설정 절차다. 클러스터 환경 비밀값 및 Tailnet 정책은 아직 구성하지 않았고, 실제 운영 배포는 실행하지 않았다.
