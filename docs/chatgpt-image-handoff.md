# ChatGPT 이미지 → AutoLog: 이슈 #2 인계

현재 개발 원본과 자체 호스팅 전환은 [MCP 통합 안내](mcp-self-hosting.md)를 따릅니다. 이 문서는 이전 Sites 연결 작업의 기록이며 당시 미완료/승인 상태를 설명합니다.

`d232015` 이후 빌드 수정, 최신 검증 결과와 구체적인 Sites FileRef 계약은
[후속 검증 보고서](issue-2-followup.md)를 참조한다. 아래 실행 기록은 최초 작업 시점의 기록이다.

## 구현 범위와 차단 요인

`work` 기준 기존 업로드 API를 재사용한다. CMS 편집기에 최근 업로드 이미지의 Markdown 표시·복사를 추가했다.
클립보드 권한이 없거나 HTTP 환경이면 읽기 전용 필드에서 직접 복사할 수 있다.
새 업로드가 실패하면 기존 본문과 마지막 성공 결과를 보존한다. 복사는 글 저장 API를 호출하지 않는다.

현재 연결된 AutoLog 도구 목록과 이슈 댓글을 확인했다. 글 CRUD·검색·카테고리·태그·연결 확인·작성 지침 도구만 있고,
파일/첨부 입력 및 `upload_image`는 없다. Sites의 향후 파일 전송 지원 여부는 이 관찰만으로 판단할 수 없다.
저장소에는 FastAPI와 Action/OpenAPI 계약이 있고 별도 Sites 앱의 도구 구현 소스는 없다.
따라서 저장소 수정으로 설치된 도구가 자동 추가되지 않는다. 직접 ChatGPT 이미지 전송은 **미해결**이다.
바이너리 전송이 입증되지 않은 업로드 Action, 별도 MCP 서버, 임의 URL 다운로드 API는 추가하지 않았다.

## 현재 사용할 수 있는 명시적 파일 선택 경로

1. ChatGPT 생성 이미지를 PNG/JPEG/WebP로 내려받는다.
2. AutoLog CMS에 로그인하고 새 글 또는 기존 글 편집 화면에서 이미지 설명과 파일을 직접 선택한다.
3. 기존 업로드 API가 성공하면 본문에 Markdown이 들어가며, 최근 업로드 Markdown과 복사 버튼이 나타난다.
4. 복사한 `![설명](https://images.example.com/uploads/...)`를 ChatGPT 대화에 붙여 넣고 원하는 글에 반영하도록 요청한다.
   글 저장 없이 이미지 링크만 전달할 수도 있다. 저장하지 않은 글의 본문을 잃지 않도록 주의한다.
5. 기존 글이면 상세/최신 버전을 조회한 뒤 기존 수정 도구로 반영하고, 충돌 시 다시 읽고 해결한다.

이미지 업로드와 글 저장은 별개다. 복사/편집 취소/글 삭제는 업로드한 오브젝트를 삭제하지 않는다.
버킷은 private이며 공개 이미지는 read-only gateway를 통해 URL을 아는 누구나 읽을 수 있다.
이 경로는 사용자가 선택한 로컬 파일만 전송하며 서버가 외부 URL을 가져오지 않는다.
`sandbox:/`, `file:`, ChatGPT 내부 파일 ID는 서버에서 다운로드할 URL로 취급하지 않는다.
브라우저는 같은 origin의 세션 쿠키를 사용한다. API/S3 토큰을 클립보드·본문·URL에 넣지 않는다.

## 기존 계약 발견 결과

| 항목 | 동작 / 구현 위치 |
| --- | --- |
| 쓰기 인증 | `backend/app/auth.py`: 유효한 Bearer API_TOKEN 또는 서명된 관리자 세션. Authorization 명시 시 토큰 우선, 쿠키로 우회 불가 |
| CSRF | 쿠키 쓰기 Origin 검사 및 Next.js 동일 호스트 검사. 쿠키 HttpOnly/SameSite=strict |
| 요청 | `POST /api/uploads`, `multipart/form-data`, `file` 바이너리 필드. Next.js는 원래 boundary와 bytes를 유지 |
| 파싱 전 제한 | `backend/app/uploads.py`의 UploadGuard: 인증 선검사, 전체 body 10 MiB + 64 KiB, chunk 누적 제한 |
| 파일 제한 | 파일 자체 ≤ 10 × 1024 × 1024 bytes. 64 KiB 단위 읽기, 빈 파일 422, 초과 413 |
| 이미지 검증 | PNG/JPEG/WebP/GIF MIME와 실제 포맷 일치, Pillow verify 및 모든 프레임 decode, 40 MP/100프레임 제한 |
| 오류 | 지원하지 않는 MIME/포맷 불일치 415, 손상/위험 데이터 422, 인증 401(토큰 미설정 503), Origin 403 |
| 저장 | `backend/app/storage.py`: backend writer credential로 private Garage put. 원본 파일명 무시, UTC/UUID key. 설정 503, S3 오류 502 상세 은닉 |
| 응답 | 201 JSON `{url,key}`. HTTPS 공개 gateway base + `uploads/YYYY/MM/UUID.ext` |
| Markdown | 기존 alt 줄바꿈 제거·대괄호/역슬래시 escape 및 URL 괄호 인코딩 결과를 본문/복사에 동일하게 사용 |
| Article | 기존 생성/수정/upsert CRUD와 버전 충돌 처리 유지. DB migration 없음 |
| 공개 읽기 | `image-gateway`: 별도 read-only S3 credential, 공개 GET/HEAD. writer credential은 backend 전용 |

원본 bytes와 metadata가 보존된다. 공개 전 개인정보가 있는 이미지인지 확인한다.

## 별도 Sites 앱 개발 인계 (아직 구현되지 않음)

Sites 구현 소유자가 실제 파일/첨부 전송 인터페이스를 먼저 입증해야 한다.
지원되는 사용자 선택 파일 또는 플랫폼 파일 핸들이 있다면 해당 앱 서버에서 그 파일의 bytes를 읽고,
기존 backend `/api/uploads`에 multipart `file`로 전송한다. 인증 토큰은 앱 서버의 보호된 설정에서만 주입한다.
모델이 이미지 bytes를 텍스트/base64로 만들어 내는 방식을 전송 구현으로 간주하지 않는다.

입증 후에만 `upload_image`를 export한다. 입력 계약은 실제 플랫폼이 지원하는 파일 핸들(출처/사용자 접근권한 확인)과
alt 설명, 응답은 `{url,key,markdown}`으로 한다. 글 저장은 기존 도구를 사용한다.
파일 소스에서 backend까지 크기·시간 제한, 스트림 중단 및 최대 10 MiB 제한을 적용한다.
리다이렉트나 다른 호스트에 AutoLog Authorization을 전달하지 않는다. 오류는 인증/형식/크기/스토리지로 구분하고
상세 토큰·서명 URL·원문 upstream 오류를 반환하거나 로그에 기록하지 않는다.

URL 전송만 가능한 경우에는 별도 설계/검증이 필요하다: 기본 거부, 허용된 정확한 HTTPS 소스,
사용자별 소유권/권한, DNS 재바인딩과 내부/loopback/link-local/metadata 주소 차단,
리다이렉트 각 hop 재검증 또는 금지, 연결/읽기 timeout, 압축 해제 이후 byte 상한,
Content-Length 부재/위조 대응 및 MIME가 아닌 실제 decode 검증이 필요하다.
현재 이 저장소는 이 fetch 경로를 제공하지 않는다.

실제 생성 이미지를 전송해 backend 201과 `{url,key}`, gateway GET 200, 이미지 hash/표시,
Article 저장/수정 및 설치된 ChatGPT 도구 호출까지 확인한 뒤에만 직접 업로드 완료로 보고한다.
이슈나 공개 로그에는 실제 호스트·계정·서비스 식별자·인증 값을 남기지 않는다.

## 테스트와 승인 후 배포

- Backend: `backend/tests/test_uploads.py` (PNG/JPEG/WebP/GIF, 위장/손상, auth/CSRF, 크기/chunk, 스토리지 오류).
  추가 회귀: 정확히 10 MiB, URL/내부 경로 텍스트 거부, 실제 multipart bytes → mock S3 → Article 생성/읽기/수정/삭제.
- Frontend: `frontend/tests/upload-proxy.test.cjs` (boundary/bytes/인증 전달, byte cap, 안전한 오류, CRUD proxy).
- Browser: `scripts/check-image-upload-ui.py` (실제 로컬 Next proxy/FastAPI, mock S3, 커서 삽입/미리보기/실패 시 본문 보존).
  테스트용 PostgreSQL과 가상환경을 명시해서 실행한다. 운영 DB를 사용하지 않는다.

승인 후 배포는 기존 frontend 빌드/rollout 절차로 진행한다. API 계약/환경변수/DB migration 변경은 없다.
Garage bucket/key/PVC, 운영 secrets, PostgreSQL 데이터, gateway/Tunnel 설정을 변경할 필요가 없다.
롤백은 frontend를 이전 이미지로 되돌린다. 업로드한 오브젝트는 유지한다.
Sites 앱의 구현·검증·재게시 작업은 별도이며 frontend 배포로 대체되지 않는다.

실행 결과와 미검증 항목은 작업 완료 보고를 참조한다. 운영 Garage/gateway 및 실제 ChatGPT 생성 파일 전송은
이번 로컬 mock 테스트만으로 검증할 수 없다.

## 이번 세션 검증 결과 (2026-10-16)

- 기준: 로컬 `work` 및 `origin/work`의 `72ab8cc`가 일치함을 확인했다.
- 원본 `.git`은 환경에서 읽기 전용이므로 원본 브랜치를 변경할 수 없다.
  `.deploy/issue2-development/repo`에 로컬 복제본을 만들고
  `feat/issue-2-image-upload-implementation` 브랜치에 변경 사항을 커밋한다.
  이 복제본은 원본 저장소만 참조하며 GitHub push는 하지 않는다.
- 기존 `backend/.venv`의 Python 3.12와 해시 고정 requirements-dev.txt 의존성을 확인/설치했다.
  Frontend는 `npm ci --ignore-scripts`로 잠금 파일 기준 설치했다.
- 이미지 검증 및 S3 adapter 단위 테스트: **12 passed**.
  PNG/JPEG/WebP/GIF, MIME 위장/손상, 애니메이션 전체 픽셀 예산과 100프레임 제한,
  Pillow 압축 폭탄 경고 거부를 확인했다. TestClient 관련 deprecation warning 1건이 있다.
- 업로드 proxy 테스트: `node tests/upload-proxy.test.cjs`로 **8 passed**.
  이 환경에서 `node --test`는 자식 테스트의 상세 결과를 출력하지 않아 직접 실행으로 확인했다.
- Frontend TypeScript 검사: **통과**.
- Production build: **실패**. Next.js가 TypeScript `--showConfig` 자식 프로세스 출력을
  JSON으로 파싱하지 못했다. 직접 실행한 TypeScript 설정 출력은 정상이다.
  빌드 성공을 주장하지 않으며 원인은 추가 실행 환경 검증이 필요하다.
- Backend 전체 회귀 테스트: **차단**. 로컬 PostgreSQL 시작 시 TCP 및 Unix 소켓 모두
  `Operation not permitted`로 실패한다. DB 테스트의 연결 오류를 확인했다.
- 브라우저 테스트: **차단**. Chromium이 시작 직후 종료되어 업로드 UI 검증까지 도달하지 못했다.
- Gateway 전체 테스트: **미완료**. 현재 환경에서 TestClient 테스트가 완료되지 않아
  제한 시간 후 중단했다. 실제 S3/HTTP 동작 성공으로 간주하지 않는다.
- 미검증: 실제 ChatGPT 생성 이미지의 도구 전송, 설치된 Sites 앱 수정/게시,
  운영 Garage 201 및 read-only gateway GET 200, 운영 배포 후 브라우저 확인.

아래 명령은 PostgreSQL·소켓·Chromium을 사용할 수 있는 개발 환경에서 전체 재검증한다.
테스트 DB는 운영 DB와 분리된 `_test` 이름이어야 한다.

```bash
(cd backend && TEST_DATABASE_URL="$YOUR_LOCAL_TEST_DATABASE_URL" .venv/bin/python -m pytest -q --tb=short)
(cd frontend && npm run typecheck && npm run build && node tests/upload-proxy.test.cjs)
(cd image-gateway && ../backend/.venv/bin/python -m pytest -q --tb=short)
TEST_DATABASE_URL="$YOUR_LOCAL_TEST_DATABASE_URL" \
  CHROMIUM_PATH=/usr/bin/chromium-browser backend/.venv/bin/python scripts/check-image-upload-ui.py
```

이번 세션에서 GitHub Push·운영 배포·Secret 변경을 수행하지 않았다.
