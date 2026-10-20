# AutoLog 이미지 연동 분석 및 개발 결과 (2026-10-20)

현재 개발 원본과 자체 호스팅 전환은 [MCP 통합 안내](mcp-self-hosting.md)를 따릅니다. 이 문서는 이전 Sites 연결 작업의 기록이며 당시 미완료/승인 상태를 설명합니다.

후속 작업의 구체적인 MCP 입력·등록 코드와 미연결 지점은
[Sites 구현 안내](sites-upload-image-implementation.md)를 참조한다.

## 현재 판정

**최종 완료 기준은 미달이다.** 실제 ChatGPT 파일 → 설치된 `upload_image` → Garage →
공개 글 표시 경로는 아직 실행할 수 없다. 로컬 전송 코드나 mock 성공을 운영 성공으로 간주하지 않는다.
커밋, push, 운영 배포, 운영 글 수정, Secret/버킷/Tunnel 변경은 수행하지 않았다.

로컬 저장소는 `work` (`72ab8cc`)이며 기존 미커밋 변경을 보존했다. 시작 당시 브랜치는
`feat/issue-2-autolog-image-handoff`였다. 이 문서의 신규 변경과 이전 작업을 구분해서 검토한다.
로컬 `origin/work`도 같은 커밋이지만 GitHub API에서 RabbitMQ 원본을 찾았으므로
로컬 원격 추적 브랜치를 최신 원격 상태로 간주하면 안 된다. 승인 후 릴리스 전 최신 `work`와 병합 검토가 필요하다.

## 기존 코드와 문제

- `backend/app/uploads.py`: `/api/uploads` multipart API, 인증 선검사, 10 MiB 파일 제한,
  전체 body +64 KiB 제한, Pillow verify/decode, 포맷/MIME 일치, 40 MP/100프레임 예산이 이미 있다.
- `backend/app/storage.py`: 기존 Garage writer, UTC/UUID4 key, HTTPS 공개 URL 검증,
  SDK 오류를 안전한 502로 변환한다. 원본 파일명은 key 생성에 쓰지 않는다.
- `backend/app/articles.py`: 이미지 업로드와 별도 저장이며 기존 CRUD 및 `expected_updated_at` 충돌 처리를 유지한다.
- `frontend/app/api/[...path]/route.ts`: multipart 원문과 boundary를 보존하고 기존 쿠키/Bearer 인증을 전달한다.
- `image-gateway/`: reader credential로 GET/HEAD만 제공한다. 수정하지 않았다.
- `backend/app/main.py`의 Action schema는 글/카테고리/태그만 노출한다. multipart endpoint를
  schema에 추가하는 것만으로 ChatGPT 첨부 파일 전달이 구현되지는 않는다.
- 연결된 AutoLog 도구 10개에 이미지 입력/업로드 도구가 없다. `check_connection`은 이번에도
  `connected: true`였지만 이는 파일 전송이나 Garage 연결 검증이 아니다.

기존 이미지/게이트웨이/프록시 문서도 확인했다. 문제의 핵심은 새 저장소나 업로드 API 부재가 아니라
별도 Sites 서버의 도구 등록과 인증된 파일 읽기 경로 부재다.

## 지원 계약과 선택 설계

[OpenAI 공식 파일 입력 계약](https://developers.openai.com/plugins/reference#define-file-inputs)을
이번에 다시 확인했다. `_meta["openai/fileParams"]`로 선언한 파일 객체는
`download_url`, `file_id`가 필수이며 `mime_type`, `file_name`도 schema에 선언하되 선택 항목이다.
이는 파일 참조 계약이며 MCP JSON 인자에 이미지 바이너리를 직접 넣는 계약이 아니다.
현재 앱의 생성 이미지 전달 성공을 보장하지도 않는다.

선택한 연결 구조는 기존 Sites 도구에서 권한이 확인된 파일을 읽고 **기존 multipart API를 호출**하는 방식이다.
base64 API나 임의 URL fetch API를 backend에 추가하지 않았다. 모델이 bytes를 생성하거나
`sandbox:/...`를 서버가 읽을 수 있는 경로로 취급하지 않는다.

Sites 앱은 소유자/활성 상태와 기존 MCP 연결을 읽기 전용으로 확인했다. 소스 열기를 위한
기존 프로젝트 credential은 받았으나 source helper의 Git fetch가
`Could not resolve host: git.chatgpt-team.site`로 실패했다. 소스 push/게시/환경변수 변경은 없었다.
따라서 기존 파일 resolver와 사용자별 토큰 보관 코드는 확인하거나 수정하지 못했다.
이 저장소의 도구 descriptor는 **제안 계약**이며 설치된 도구가 아니다.

## 이번에 추가한 코드

| 파일 | 변경 |
| --- | --- |
| `frontend/lib/autolog-image-transport.ts` | 권한 확인을 마친 Blob을 기존 API에 전달하는 서버용 전송 함수 |
| `frontend/lib/image-upload.ts` | Markdown escape/URL 검사와 편집기 인스턴스별 SHA-256 성공 캐시 |
| `frontend/components/ArticleEditor.tsx` | 설명 필수, 같은 bytes 재사용, 선택한 기존 본문을 삭제하지 않고 삽입 |
| `frontend/tests/image-upload.test.cjs` | 전송 bytes/인증/오류/응답 제한/Markdown/동시 중복 검증 6개 |
| `scripts/check-image-upload-ui.py` | 같은 파일 재선택 시 추가 요청 없음과 선택 본문 보존 검증 추가 |
| `scripts/prepare-rabbitmq-images.py` | 검토된 로컬 SVG 3개를 PNG로 변환하고 hash manifest 생성 |
| `docs/autolog-image-upload-review.md` | 분석, 검증 경계, 마이그레이션 및 릴리스 절차 |

전송 함수는 MCP 서버나 FileRef resolver가 아니다. 기존 Sites 서버에 파일 권한 검증을 연결하기
전에는 `upload_image`를 노출하면 안 된다. `connection`은 기존 인증된 사용자 설정에서 서버가
읽어야 하며 모델 입력에 토큰/backend 주소/이미지 destination을 넣으면 안 된다.
이 함수는 원격 파일 URL을 가져오지 않는다. 외부 URL fetch에 대한 SSRF 방어를 구현했다고 주장하지 않는다.

함수는 지원 MIME/빈 파일/10 MiB 상한을 사전 검사하고 실제 decode 검증은 backend가 수행한다.
고정 HTTPS backend, redirect 금지, Cookie 미전송, 30초 timeout, 응답 16 KiB 상한,
UUID4 key와 설정된 public base 일치를 확인한다. 201 성공 이후에만 Markdown을 반환한다.
원문 upstream 오류는 반환하지 않고 상태별 안전한 오류를 만든다. 네트워크 실패는 자동 재시도하지 않는다.
실제 서버가 저장한 뒤 응답이 끊겼을 수 있으므로 결과 불명 상태에서 재시도하면 중복될 수 있다.

base64 팽창 없이 원본 파일을 보낸다. 파일 hash용 버퍼는 최대 10 MiB이고 캐시에는 파일 bytes를
보관하지 않는다. FormData/네트워크 버퍼 비용은 별도로 존재하므로 Sites 쪽에서 동시 업로드 수를
제한해야 한다. 캐시는 같은 편집기에서 최근 100개 성공 결과만 재사용하고 실패는 캐시하지 않는다.
브라우저 HTTPS/WebCrypto가 없으면 업로드는 유지하되 hash 재사용은 하지 않는다.
새로고침, 다른 탭/사용자, 다른 프로세스에 걸친 영속적 멱등성은 제공하지 않는다.

글 저장은 기존 도구를 사용한다. 업로드 실패 시 Markdown을 추가하지 않고 기존 본문을 보존한다.
이미지 없이 저장하는 경우 실패 사실을 응답에 명시한다. 글 저장 실패 시 성공한 이미지 URL/key를
보관하고 글 저장만 다시 시도한다. 업로드와 글 저장을 하나의 원자적 트랜잭션이라고 설명하지 않는다.

## 테스트 결과

| 검사 | 이번 실행 |
| --- | --- |
| Backend 이미지 validation/UploadGuard/S3 adapter | 18 passed (DB/실제 S3 없음) |
| Gateway 응답/reader adapter | 10 passed (mock) |
| 기존 frontend proxy | 8 passed (mock fetch) |
| 신규 전송/중복/Markdown | 6 passed (mock fetch; 실제 이미지 decode는 backend 검증) |
| TypeScript | 통과 |
| Production build | `AUTOLOG_BUILD_WORKER_THREADS=1 npm run build` 통과 |
| Backend 전체 CRUD 회귀 | PostgreSQL 연결 오류로 첫 fixture에서 중단 |
| Gateway 전체 HTTP 테스트 | 25초 timeout 종료 (exit 124); 완료 아님 |
| 브라우저 | `/usr/bin/chromium-browser` Snap 실행 실패; UI assertion 미도달 |
| 소켓 진단 | 로컬 AF_INET 생성도 EPERM; 환경 제약 확인 |
| 실제 ChatGPT 파일/Garage | 미실행 |
| 외부 gateway | curl이 DNS 해석 실패; HTTP status/Content-Type 확인 불가 |

`curl -I https://img.myhomlab.win/uploads/...png`는 DNS 단계에서 실패했다. 이 실행 환경의
해석 실패만으로 운영 Cloudflare DNS/Tunnel 장애를 확정하지 않는다. 실제 object를 읽은 것도 아니다.
운영 API/내부 Kubernetes/Garage 자격증명 readiness를 확인할 환경 도구는 제공되지 않았다.

## RabbitMQ 마이그레이션 준비

로컬 원본 경로에는 파일이 없었지만 GitHub connector로 원격 `work`에서 세 SVG를 읽었다.
추적 파일을 덮어쓰지 않고 `.deploy/rabbitmq-source/`에 저장했으며 `provenance.json`에 원본 URL/blob SHA를 보관했다.
원본 SHA: upgrade `d411889d232bf943fba809b34be0d658b4739925`,
message `c06d2b90301395ef17ecb55afef63490f58fca43`,
incident `d2421944d91f8c189dc46c702d9af61b3717d0f8`.

설치된 sharp 0.35.5/librsvg 2.63.2로 3개 PNG를 만들고 Pillow decode까지 확인했지만
이미지를 열어 보니 한글이 깨졌다. 이 환경에 한글 글꼴이 없으므로 결과는
`.deploy/rabbitmq-images-font-missing/`에 별도 보관하고 **업로드용으로 승인하지 않았다**.
변환 스크립트에 한글 글꼴 필수 검사와 임시 Fontconfig 설정을 추가했다. DejaVu 글꼴 거부도 확인했다.
수정 후 한글이 정상인 최종 PNG 변환은 아직 실행하지 못했다.

글꼴이 준비된 개발 환경에서:

```bash
backend/.venv/bin/python scripts/prepare-rabbitmq-images.py \
  --source .deploy/rabbitmq-source \
  --font-file /usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc \
  --output .deploy/rabbitmq-images-reviewed
```

`rsvg-convert`가 있으면 사용하고, 없으면 설치된 frontend sharp를 사용한다.
외부에서 받은 임의 SVG를 이 스크립트에 넣지 않는다. 출력 디렉터리를 덮어쓰지 않는다.
세 PNG의 한글/내용/여백을 시각 검토한 다음 아래 순서로 이전한다.

1. 실제 ChatGPT 업로드 경로 및 gateway가 검증된 뒤, 검토된 PNG를 기존 API로 각각 한 번 업로드한다.
   기존 서버 토큰은 보호된 설정으로 전달한다. 원본 hash별 성공한 `{url,key}`를 별도 manifest에 기록한다.
   성공 기록이 있으면 재업로드하지 않는다. secret을 manifest나 명령행에 넣지 않는다.
2. 반환 URL의 내부/외부 GET·HEAD, MIME, 다운로드 SHA-256을 원본 PNG와 대조한다.
3. 기존 글 2개의 최신 본문과 `updated_at`을 읽어 로컬 백업한다. 원본 GitHub URL의 정확한
   세 문자열만 성공한 gateway URL로 치환하고 의미 있는 alt를 유지한다. 원본 본문과 diff를 검토한다.
4. **운영 글 수정에 대한 사용자 승인 후에만** 기존 update 도구에 전체 보존된 본문과
   `expected_updated_at`을 보낸다. 충돌이면 다시 읽고 diff를 만든다. 실패를 성공으로 보고하지 않는다.
5. 공개 페이지의 `<img>` 조회/렌더링을 확인한다. 문제 시 승인된 백업 본문으로 복구한다.
   기존 GitHub 원본이나 Garage object를 자동 삭제하지 않는다.

## 별도 앱 구현과 승인 후 릴리스

1. 네트워크가 가능한 개발 환경에서 **기존 AutoLog Sites** 소스를 연다. 사용자별 연결/토큰 경로를
   확인하고 기존 identity/access scope를 유지한다. 별도 저장소나 MCP 서버를 새로 만들지 않는다.
2. 공식 FileRef 계약을 실제 첨부/생성 이미지로 확인한다. 플랫폼이 보장하는 파일 권한을 검증하고
   host의 파일 reader로 bytes를 읽는다. URL fetch가 필요하다면 활성화 전 정확한 HTTPS host/path
   allowlist, 공인 IP 검증/재바인딩 방어, redirect 금지, 압축 해제 후 10 MiB, timeout을 구현한다.
   AutoLog 인증을 파일 다운로드 host에 전달하지 않는다. 검증할 수 없으면 fail closed한다.
3. 이 저장소의 `autolog-image-transport.ts`와 `image-upload.ts`를 기존 서버 모듈로 연결하고
   제안 descriptor의 `upload_image`를 등록한다. 오류는 도구의 `isError`로 표준화한다.
   성공 `{url,key,markdown}`만 모델에 반환한다. 파일 읽기 구현과 도구 등록은 아직 미완료다.
4. 개발 환경에서 실제 ChatGPT 파일 hash → backend 201 → Garage key → 내부 gateway GET/HEAD
   200 → 외부 GET/HEAD 200 + MIME/hash → 공개 페이지 표시를 검증한다. 없는 key 404,
   POST/PUT/DELETE 405도 확인한다. DB CRUD 전체 회귀와 브라우저 검사를 다시 실행한다.
5. 외부 경로 오류는 DNS → Tunnel/cloudflared → Service → NetworkPolicy → gateway → Garage로
   구간별 확인한다. [기존 gateway 문서](image-gateway.md)의 진단 절차를 사용한다. 초기화/교체하지 않는다.
6. 코드 리뷰와 별도 승인 이후에만 커밋/push 및 기존 frontend/플러그인의 배포 절차를 수행한다.
   이번 신규 변경에는 backend/DB/gateway 설정 변경이나 migration이 없다. Sites 앱 재게시와
   ChatGPT 도구 갱신은 frontend 배포와 별개다. audience/인증 범위를 유지한다.
7. 배포 후 실제 ChatGPT에서 파일 업로드 도구가 보이는지와 발행 요청 전체 경로를 다시 확인한다.
   기존 글 이전 승인은 별도로 받는다. 롤백은 frontend와 Sites를 이전 버전으로 복구하고
   업로드 object와 운영 데이터는 보존한다.

재검증 명령은 [기존 인계](chatgpt-image-handoff.md)와 [후속 검증](issue-2-followup.md)을 참고하고
frontend에서 `node tests/image-upload.test.cjs`도 실행한다. 이전 문서의 날짜별 결과는 이번 결과로 대체해 해석하지 않는다.
