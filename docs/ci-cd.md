# AutoLog CI/CD 운영 가이드 (승인형 배포)

**목적:** `work` 브랜치의 AutoLog 변경을 GitHub Actions가 테스트하고 GHCR에 SHA 태그 이미지를 발행하며, 운영 Kubernetes에는 **명시적인 승인 후** 애플리케이션 이미지만 갱신한다.

이 문서의 워크플로는 **설치 코드/절차**다. GitHub 설정, GHCR 권한, 배포 러너, 운영 kubeconfig/백업이 준비되지 않으면 실제 배포는 진행되지 않는다. 2026-10 시점의 Codex 전용 개발 VM에는 운영 클러스터 자격증명을 추가하지 않는다.

## 운영 구조

```text
Codex CLI (개발 VM, 로컬 변경)
  -> 리뷰할 커밋을 GitHub 원격 개발 브랜치에 별도 Push (승인 후)
  -> PR -> work
      -> CI: Backend pytest / Gateway pytest / Frontend tsc + proxy tests + Next build
      -> CI: Kustomize + render 검증 (클러스터 접속 없음)
  -> PR 승인 후 work로 merge
      -> CI 재검증
      -> GHCR: backend, frontend, image-gateway
         태그 sha-<40자리 commit SHA>
  -> 별도 수동 CD 요청 + GitHub Environment: production 승인
      -> 전용 self-hosted runner + 최소 권한 kubeconfig
      -> 배포 미리보기
      -> lifelog 3개 Deployment 이미지 교체 + rollout status
      -> 실패 시 변경한 Deployment rollout undo 시도
```

**하지 않는 작업:** GitHub Actions가 DB, PVC, Secret, ConfigMap, Garage, NetworkPolicy, Cilium, Namespace 또는 Alembic migration을 자동 apply하지 않는다.

## 포함된 파일

| 파일 | 역할 |
| --- | --- |
| `.github/workflows/autolog-ci.yml` | PR/Push 테스트; 성공한 `work` 커밋 이미지만 GHCR에 빌드·업로드 |
| `.github/workflows/autolog-deploy.yml` | 수동 트리거 전용 운영 배포 (자동 Push 배포 없음) |
| `scripts/deploy-autolog.sh` | 계획 확인(기본) / 승인된 배포 및 오류 시 Deployment 롤백 |
| `docs/ci-cd.md` | 최초 셋업, 한계, 릴리스/복구 절차 |

## 1. CI 사용법

GitHub Repository → **Actions**에서 워크플로 활성화를 확인한다. 자동 실행은 다음 조건이다.

- `work`을 대상으로 하는 PR: **테스트만 실행**, 이미지 Push는 하지 않는다.
- `work`에 Push: 모든 테스트 통과 후 **GHCR 이미지 3개**를 빌드하고 발행한다.
- `workflow_dispatch`: 수동 CI 검사만 실행, GHCR Publish는 수행하지 않는다.

현재 소스는 `frontend/package-lock.json`과 Python 해시 잠금 `requirements-dev.txt`를 사용한다. 검증 작업은 모두 GitHub가 제공하는 러너에서 돌아가며 운영 클러스터나 DB에 접근하지 않는다.

이미지 태그 예시(실제 사용 시 **성공한 work commit SHA**로 변경):

```text
ghcr.io/dbdtm123-hub/autolog/backend:sha-<40_HEX_SHA>
ghcr.io/dbdtm123-hub/autolog/frontend:sha-<40_HEX_SHA>
ghcr.io/dbdtm123-hub/autolog/image-gateway:sha-<40_HEX_SHA>
```

`sha-` 태그는 재현에 도움이 되지만 태그 자체가 레지스트리에서 기술적으로 변경 불가능한 것은 아니다. 완전한 이미지 고정이 필요하면 빌드 결과의 `@sha256` digest까지 검증하고 배포하는 방식으로 확장한다.

GitHub Settings → Actions에서 GITHUB_TOKEN의 **Packages 쓰기 권한/조직 패키지 정책**을 확인한다. CI의 `publish` Job에만 `packages: write` 권한이 있다. 별도 PAT를 워크플로에 넣지 않는다.

**현재 Codex 로컬 커밋은 원격 `work`에 자동 반영되지 않는다.** PR로 검토·Push·Merge해야 해당 버전의 CI 이미지가 생긴다.

## 2. 최초 CD 셋업 — 자동 실행되지는 않음

**주의: `main`이 기본 브랜치이고 현재는 README 중심이다.** GitHub의 수동 `workflow_dispatch` 버튼은 워크플로가 *기본 브랜치에도 존재*해야 정상 활성화된다. 이 PR을 `work`에만 합치는 것으로는 수동 배포 버튼이 안 보일 수 있다. 실제 CD 운영 전에는 보호된 변경 절차로 같은 배포 워크플로를 기본 브랜치에도 반영해야 한다. 운영 인프라에는 자동 적용하지 않는다.

필요한 준비 작업:

1. GitHub Settings → Environments에서 `production` 환경을 만들고 **Required reviewers**와 가능한 배포 브랜치/태그 제한을 설정한다. Repository 플랜에 따라 승인 기능이 달라질 수 있다.
2. 배포 전용 GitHub Actions **self-hosted runner** 1대를 구성하고 라벨 `autolog-deployer`를 지정한다. 개인 개발 VM의 Codex 환경에 운영 kubeconfig를 주지 말고 별도 신뢰 가능한 관리 호스트 또는 격리된 러너를 사용한다.
3. 해당 러너에 `kubectl` 및 관리용 `KUBECONFIG` 환경 변수를 설정한다. Kubernetes RBAC는 `lifelog` 네임스페이스의 필요한 Deployment 조회·patch/rollout에 필요한 권한만 부여한다. 운영 DB·Secret 열람이나 cluster-admin 권한은 주지 않는다. kubeconfig 파일 및 인증서를 Git/Actions 로그에 출력하지 않는다.
4. Kubernetes 워커에서 GHCR의 이미지 3개를 pull할 수 있는지 확인한다. 비공개 패키지라면 **기존 Kubernetes imagePullSecret**을 적절한 Deployment ServiceAccount/Pod에 연결해야 한다. 이 PR은 Secret을 생성하거나 수정하지 않는다.
5. 실제 배포 전에 DB 백업과 복구 절차를 확인하고, 해당 커밋에서 **Alembic migration이 필요한지** 사람이 확인한다. 필요하면 별도 승인된 마이그레이션 작업을 먼저 수행해야 한다. 이 자동 CD는 migration을 실행하지 않는다.

**확인:** 관리자용 kubeconfig를 Actions 저장소 Secret으로 직접 복사하거나 공개 GitHub 호스티드 러너에 전달하지 않는다. Self-hosted runner는 노출/유출 가능성이 있으므로 악의적 PR 코드를 실행하지 않는 배포 전용으로 관리한다.

## 3. 직접 배포 계획 확인 (읽기 전용)

`KUBECONFIG`가 설정된 **배포 관리 호스트**에서:

```bash
export KUBECONFIG=/path/to/least-privilege-kubeconfig
# 성공한 work CI의 commit SHA
RELEASE_SHA="<40_HEX_SHA>"
bash scripts/deploy-autolog.sh "$RELEASE_SHA"
```

`--apply`가 없으면 네임스페이스와 Deployment 존재만 검사하고 바꿀 이미지 목록을 출력한다. 어떤 클러스터 리소스도 수정하지 않는다.

## 4. 승인 후 배포

수동 `workflow_dispatch` 화면에서 **검증된 work 커밋의 SHA**와 확인 문자열 `DEPLOY-LIFELOG`를 입력한다. 이어서 `production` Environment의 승인자가 확인한다.

러너에서 실행되는 순서:

1. 선택한 커밋이 실제 `work` 기록에 포함됐는지 검증한다.
2. 앱 Deployment 세 개의 변경 계획을 확인한다.
3. Backend, image-gateway, Frontend 이미지 태그만 순서대로 갱신한다.
4. 각 Deployment에 대해 `rollout status --timeout=300s`를 기다린다.
5. 중간 단계에서 실패하면 변경한 Deployment를 역순으로 `rollout undo` 시도한다.

명령을 수동 실행해야 하는 예외적인 경우에만 다음과 같이 별도 확인 변수를 사용한다.

```bash
export AUTOLOG_CONFIRM_DEPLOY=DEPLOY-LIFELOG
bash scripts/deploy-autolog.sh "$RELEASE_SHA" --apply
```

`kubectl rollout undo`는 **애플리케이션 Deployment의 이전 ReplicaSet 설정만 되돌린다.** 데이터베이스, 애플리케이션이 쓴 새 데이터, Storage 변경을 복원하지 않는다. 따라서 DB 마이그레이션과 보존 데이터 변경은 이 흐름에서 제외한다.

배포가 성공해도 **브라우저/외부 HTTPS GET·이미지 업로드·글 작성** 같은 실제 사용 흐름은 별도로 확인해야 한다.

## 5. 릴리스 전 체크

- PR의 CI Job 4개와 이미지 Publish가 성공했는가?
- 대상 커밋이 `work`의 검증된 커밋인가?
- GHCR 이미지 3개가 해당 SHA 태그로 존재하고 워커에서 pull 가능한가?
- DB 백업, 앱과 DB 스키마의 호환성, 유지보수 창 확인이 끝났는가?
- 운영 권한이 없는 개발 VM에서 임의의 배포나 Secret 수정을 하지 않았는가?
- 배포 후 이미지 GET과 AutoLog API/웹이 정상인가?

## 6. 현재 상태와 제약

이 문서를 포함한 PR은 **설계 및 워크플로 코드 추가** 작업으로, 자동 CI가 GitHub에서 실제 통과했는지, GHCR 이미지가 이미 발행됐는지, self-hosted runner가 등록됐는지, Environment reviewer가 설정됐는지는 별도 확인해야 한다. **운영 배포는 실행하지 않았다.**

현재 실제 사용 중인 OpenStack/k8s와 코드 저장소의 매니페스트/이미지 태그가 완전히 같은지도 검증해야 한다. 실제 운영 Deployment 명칭과 Container 이름이 여기서 가정한 `lifelog-backend`/`backend`, `image-gateway`/`image-gateway`, `lifelog-frontend`/`frontend`와 맞는지 계획 모드로 먼저 검증한다.
