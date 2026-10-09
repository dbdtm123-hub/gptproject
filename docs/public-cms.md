# 공개 기술 블로그와 관리자 CMS

## 권한

| 요청 | 비로그인 | 관리자 세션 | ChatGPT Bearer Token |
| --- | --- | --- | --- |
| Article 목록·검색·상세 | published만 | 전체, 상태 필터 가능 | published만 |
| Category·Tag | published 글에 사용된 값만 | 전체 | published 글에 사용된 값만 |
| draft 상세·status=draft | 401 | 허용 | 401 |
| Article 생성·upsert·PATCH·DELETE | 401 | 허용 | 허용 |
| 이전 Entry 읽기·capabilities | 401 | 허용 | 401 |
| 이전 Entry/ingest 쓰기 | 401 | 허용 | 허용 |

slug를 알아도 비로그인 목록의 slug 검색에는 draft가 나오지 않으며 UUID 상세는 401이다.
공개 상세의 related_articles에는 published ID만 있고 source_reference는 null로 숨긴다.
공개 검색, total, 카테고리와 태그에 draft가 섞이지 않는다. 데이터 모델/migration은 변경하지 않는다.

## 로그인과 브라우저

- `/`와 published 상세는 로그인 없이 열람한다. 공개 목록은 10개씩 번호 페이지네이션을 제공한다.
- `/login`은 APP_USERNAME/APP_PASSWORD를 backend `/api/auth/login`에 전달한다.
- Backend가 비밀번호에서 파생한 HMAC-SHA256 키로 8시간 만료 세션을 서명한다. 비밀번호/사용자 변경 시 기존 세션은 무효다.
- Next.js는 세션을 autolog_admin HttpOnly, SameSite=Strict, Path=/ 쿠키로만 설정한다. HTTPS에서는 Secure=true다.
- `/admin`에서 전체/draft/published 필터, 직접 작성, Markdown 미리보기, 수정·상태 변경·삭제를 수행한다.
- `/api/auth/logout` POST는 쿠키를 제거한다. stateless 세션은 서버 목록으로 보관하지 않으며 발급된 세션 자체는 만료 또는 비밀번호 변경까지 유효하다.
- Next.js proxy는 관리자 페이지 접근을 backend에 검증하고, backend는 모든 읽기/쓰기 요청의 권한을 다시 검증한다.
- 세션을 사용하는 로그인·쓰기·로그아웃은 Next.js에서 다른 Origin을 403으로 거절한다. JSON 요청만 허용한다.
  Backend 직접 cookie 쓰기도 Origin이 설정된 CORS 목록에 없으면 403이다. 외부 Bearer 쓰기는 cookie 인증을 사용하지 않는다.
- 잘못된 로그인 10회/분 이후 429를 반환한다. 제한은 backend 프로세스/IP 단위이며 replica별로 독립적이다.
- API_TOKEN은 backend 전용이다. Frontend 환경변수·이미지·bundle·JSON·HTML·브라우저 요청에 주입하지 않는다.

기존 Basic 로그인 대신 폼 로그인을 사용한다. Cloudflare Tunnel/DNS/라우팅은 변경하지 않는다.
Cloudflare Access가 별도로 전체 사이트를 보호한다면 published 공개 여부는 해당 Access 정책에도 달려 있다.

## 기존 Kubernetes 설치 업데이트

1. backend와 frontend 두 이미지를 새 고유 태그로 Podman build/push한다. DB/PVC와 기존 Secret은 유지한다.
2. backend에 lifelog-app Secret의 APP_USERNAME/APP_PASSWORD를 추가한다. Frontend는 BACKEND_URL만 사용한다.
   수정한 `k8s/backend/deployment.yaml`, `k8s/frontend/deployment.yaml`을 README의 renderer로 적용한다.
   `kubectl set image`만 실행하면 새 환경변수 변경은 적용되지 않으므로 Deployment manifest도 갱신해야 한다.
3. Backend readiness가 정상인 뒤 frontend를 rollout한다. 이번 권한 변경에는 새 migration이 없다.
4. 기존 Cloudflare Tunnel의 backend Service 연결을 유지한다. HTTPS에서 Secure 쿠키가 설정되는지 확인한다.
5. CORS_ORIGINS에는 실제 공개 HTTPS URL을 넣는다. ACTION_SERVER_URL도 해당 URL로 설정한다.

기존 Secret 이름은 lifelog-api(API_TOKEN), lifelog-app(APP_USERNAME/APP_PASSWORD)다.
토큰 교체 시 backend만 재시작하고 GPT Action 인증 값을 교체한다. 관리자 암호 교체 시 backend를 재시작하고 다시 로그인한다.

## 운영 호스트에서 실행할 업데이트 스크립트

Podman과 대상 클러스터 kubeconfig/kubectl이 있는 관리 호스트에서 실행한다.
현재 Deployment 이미지의 registry/repository를 재사용하고 커밋 해시를 고유 태그로 사용한다.
frontend/backend image와 환경변수만 함께 patch하며 기존 Service, Tunnel, DB/PVC, Secret 값을 변경하지 않는다.
변경 전 server-side dry-run으로 admission을 확인한다. 새 migration은 실행하지 않는다.

```bash
git switch work
git pull --ff-only origin work
PUBLIC_URL=https://autolog.myhomlab.win bash scripts/deploy-public-cms.sh
```

스크립트는 현재 Kubernetes context와 두 image 경로를 표시하고 Podman registry 로그인을 요청한다.
현재 이미지가 registry-qualified가 아니거나 저장소를 바꾸려면 BACKEND_REPO/FRONTEND_REPO를 명시한다.
다른 namespace/architecture에는 NAMESPACE/PLATFORM을 설정한다. 기본값은 lifelog/linux/amd64다.
운영 호스트의 작업 트리가 깨끗해야 실행되며 빌드·push·rollout 실패 시 멈춘다.
기존 CORS_ORIGINS/ACTION_SERVER_URL은 실제 도메인으로 설정돼 있어야 한다.

```bash
kubectl -n lifelog get pods
kubectl -n lifelog logs deployment/lifelog-backend --tail=100
kubectl -n lifelog logs deployment/lifelog-frontend --tail=100
kubectl -n lifelog describe deployment lifelog-backend
kubectl -n lifelog describe deployment lifelog-frontend
# 배포한 버전 문제가 확인되면 양쪽을 이전 Pod template(image/env 포함)으로 복구한다.
kubectl -n lifelog rollout undo deployment/lifelog-backend
kubectl -n lifelog rollout undo deployment/lifelog-frontend
```

이 클라우드에서는 스크립트의 shell/Python 문법과 생성 patch를 검사했다.
운영 kubeconfig/Podman이 없어 실제 registry push/server-side admission/원격 rollout은 검증하지 않았다.

## 확인

```bash
curl --fail https://YOUR_HOST/api/articles
curl --fail https://YOUR_HOST/api/categories
curl --fail https://YOUR_HOST/api/tags
# 인증 없는 쓰기는 HTTP 401이어야 한다.
curl -i -X POST https://YOUR_HOST/api/articles -H 'Content-Type: application/json' \
  --data '{"title":"test","category":"test","summary":"test","content_markdown":"# test"}'
```

/login → /admin에서 draft 생성 → 공개 브라우저에서는 검색/UUID 조회 불가 → 관리자에서 published로 변경 → 공개 상세 확인 → 관리자 삭제를 검증한다.
별도의 보호된 Token 파일로 scripts/submit-article.py를 호출해 GPT 쓰기를 재현한다. Token으로 draft GET은 허용하지 않는다.
실제 운영 클러스터의 rollout은 kubeconfig/GHCR 인증을 가진 관리 호스트에서 수행한다.
