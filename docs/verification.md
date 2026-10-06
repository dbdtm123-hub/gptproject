# Article 중심 기술 블로그 검증

## 결과

실제 PostgreSQL과 production frontend/backend 컨테이너에서 OpenAI 키 없이 검증했다.
전체 backend 테스트 **81 passed**, 실패/skip 없음. Starlette/httpx deprecation warning 1개는 남아 있다.
Frontend 타입 검사, 고정 lockfile 설치, production 이미지 빌드가 통과했다.
운영 Podman/containerd 원칙은 유지하고 이 클라우드에서 Docker Compose는 개발 검증에만 사용했다.

| 대상 | 확인한 동작 |
| --- | --- |
| Article 모델/API | 완성된 Markdown, slug, category/subcategory, tags, source, related links, draft/published, 작성/수정 시각 저장 |
| OpenAI 비의존 | 실행 중 backend의 OPENAI_API_KEY 빈 값, legacy AI flag false. Article CRUD/upsert에 provider 호출 없음 |
| 인증 | 외부 Article API의 토큰 누락/오류는 401. 올바른 Bearer는 성공. 웹은 기존 Basic 로그인 후 서버 토큰 사용 |
| upsert | 신규 201, 기존 200. 같은 slug/명시적 ID 갱신, replace/append, 선택 metadata 보존, stale timestamp 409 |
| 동시성/검증 | 동시에 8개 upsert를 보내도 글 ID 하나, 모든 추가 본문 보존. 잘못된 입력 및 병합 길이 초과는 저장하지 않음. 본문 들여쓰기/개행을 원형 보존 |
| 카테고리/태그/검색 | 기존 Category 재사용, 새 이름 생성, 태그 목록과 category/tag/status/slug 필터, 본문 검색 |
| 관련 글 | 유효 UUID 연결, 없는 글/자기 참조 거절, 삭제 시 링크 정리 |
| schema | checked-in Action JSON과 실제 backend 출력 일치. 서버 URL만 배포별 설정 값으로 허용 |
| 브라우저 | NFS 글 API 입력 → 열린 목록 자동 반영 → 필터/검색 → Markdown 상세/표/코드 → 관련 글 → 이어쓰기 자동 반영 |
| 직접 작성 | 본문 입력, Markdown 미리보기, 저장, 상태 변경, 수정, 삭제 성공. /legacy도 열람 가능 |
| 안전성 | raw script와 javascript 링크 실행 차단. HTML/JSON/JS 응답 및 브라우저 요청·static bundle에 API_TOKEN 값 없음 |
| 실행/배포 설정 | Compose의 토큰 일치와 필수 변수, Kubernetes의 lifelog-api Secret 참조 유지. 새 필수 환경변수 없음 |

## migration

개발 DB에서 `0001 → 0002`를 적용하고 Alembic check의 “No new upgrade operations detected”를 확인했다.
추가로 새 독립 테스트 DB에 Entry/Category를 넣고 `0001 → 0002 → 0001 → 0002`를 실행했다.
모든 단계에서 기존 Entry 원문/제목과 Category가 보존됐다. 테스트 DB는 검증 후 제거했다.
운영에서는 전진 migration을 사용한다. downgrade는 Article 테이블을 삭제하므로 백업 없이 운영에서 실행하지 않는다.

## 단계별 변경 파일

1. Backend: app/models.py, article_schemas.py, articles.py, main.py,
   alembic/versions/0002_articles.py, tests/test_articles.py와 기존 Action schema 회귀 검사.
2. UI: app/page.tsx, app/articles/, components/ArticleEditor.tsx와 Markdown.tsx, lib/api.ts,
   인증 프록시, globals.css, layout.tsx, package.json/package-lock.json. 이전 화면은 app/legacy/에 보존.
3. 연동/운영: docs/actions.openapi.json, chatgpt-actions.md, architecture.md,
   examples/nfs-article.json, scripts/submit-article.py, cloud-dev.py, README와 frontend README.

## 재현

README의 개발 DB 비밀번호·APP_PASSWORD·API_TOKEN 설정과 전용 `_test` DB 준비를 먼저 수행한다.
기존 volume 비밀번호나 데이터는 초기화하지 않는다. OpenAI 키를 설정할 필요가 없다.

```bash
# 일반 로컬 개발
test -f .env || cp .env.example .env
# .env의 DB/웹 비밀번호와 32자 이상 API_TOKEN을 안전하게 설정한다.
docker compose up --build -d --wait

# 이 클라우드의 프록시 환경에서는 대신:
backend/.venv/bin/python scripts/cloud-dev.py build
backend/.venv/bin/python scripts/cloud-dev.py start

cd backend
TEST_DATABASE_URL="$YOUR_TEST_DATABASE_URL" OPENAI_API_KEY='' ENABLE_OPENAI_CLASSIFICATION=false \
  .venv/bin/python -m pytest -q
.venv/bin/python -m alembic check
cd ../frontend
npm run typecheck
npm run build
```

[Action 문서](chatgpt-actions.md)에 따라 보호된 토큰 파일로 다음 요청을 보낸다.

```bash
python scripts/submit-article.py --url http://127.0.0.1:3000 \
  --token-file "$SECRET_DIR/API_TOKEN" \
  --payload docs/examples/nfs-article.json --upsert
```

이 CLI도 실제 실행해 글 저장과 삭제를 확인했다. 브라우저에서 `/articles/반환된UUID`를 열어
구성 환경, 문제, 원인, 해결, 최종 구조와 코드 블록을 확인한다. 테스트가 만든 글과 미사용 카테고리는 정리했다.

## 검증 경계

이 테스트는 ChatGPT가 완성된 Article을 작성해 보내는 요청을 외부 클라이언트로 재현했다.
실제 Custom GPT의 자연어 분석·Action 호출은 외부 HTTPS 주소와 인증 등록 후 확인해야 한다.
대상 OpenStack 클러스터의 kubeconfig/GHCR 인증 정보가 없어 실제 registry push와 Kubernetes rollout은 실행하지 않았다.
기존 Kubernetes 구성과 배포 절차를 유지하며 새 migration Job 완료 후 API/UI를 업데이트해야 한다.
