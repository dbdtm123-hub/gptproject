# Issue #2 후속 검증: d232015 기준

현재 개발 원본과 자체 호스팅 전환은 [MCP 통합 안내](mcp-self-hosting.md)를 따릅니다. 이 문서는 이전 Sites 연결 작업의 기록이며 당시 미완료/승인 상태를 설명합니다.

## Production Build 원인과 수정

Node 22.23.3 / Next.js 16.3.8 / TypeScript 5.9.3에서 기존 오류를 재현했다.
`tsc --showConfig`를 직접 실행하면 정상 JSON이 나오며, `tsc --noEmit`도 통과한다.
Next.js의 기본 `experimental.useTypeScriptCli=true`는 자식 프로세스 stdout을 수집한 뒤
JSON으로 파싱한다 (`next/dist/lib/typescript/runTypeScriptCli.js`).

이 실행 환경에서는 Node 자식 프로세스를 `stdio: pipe`로 실행하면 단순한
`console.log`조차 출력이 비어 있다. `spawnSync`는 `error.code=EPERM`과 빈 stdout을
반환했으며, 같은 자식의 파일 쓰기 side effect는 실행됐다. `stdio: inherit`에서는
출력이 보였다. 비동기 spawn은 종료 코드 0과 빈 stdout을 반환하므로 Next.js가
`JSON.parse('')`에서 실패한다. detached 여부와 무관하게 재현됐다.
애플리케이션의 TypeScript 오류나 tsconfig 문법 오류로 확인된 것은 없다.

`frontend/next.config.ts`에 선택적 `AUTOLOG_BUILD_WORKER_THREADS=1` 모드를 추가했다.
이때 TypeScript compiler API로 검사하고, 페이지 생성 worker는 thread로 실행하며,
webpack은 별도 build process 없이 실행한다. 타입 검사 생략 옵션은 사용하지 않았다.
일반 환경에서는 기존 기본 설정을 유지한다. node_modules/lockfile은 수정하지 않았다.

```bash
cd frontend
AUTOLOG_BUILD_WORKER_THREADS=1 npm run build
npm run typecheck
node tests/upload-proxy.test.cjs
```

실제 production build는 컴파일, TypeScript 검사, 페이지 생성, trace 수집,
최종 라우트 출력까지 성공했다. 빌드 성공은 운영 배포나 실제 서비스 접속 검증을 의미하지 않는다.

## 테스트 결과

| 검사 | 이번 실행 결과 |
| --- | --- |
| Production build (위 모드) | 통과 |
| TypeScript | 통과 |
| 업로드 proxy | 8 passed; node:test 파일을 직접 실행해 개별 assertion 결과 확인 |
| Backend 이미지 validation / adapter / UploadGuard | 18 passed |
| Gateway 응답 검증 / reader credential adapter | 10 passed |
| Backend 전체 회귀 | DB fixture에서 연결 오류로 중단 |
| Gateway 전체 HTTP 테스트 | 최소 FastAPI TestClient에서도 hang이 재현됨; 시간 제한 후 중단 |
| 실제 Garage / gateway 및 ChatGPT 생성 파일 | 미검증 |

Backend에 DB와 HTTP 서버가 필요 없는 UploadGuard 테스트 6개를 추가했다.
인증 전 body 미소비, Content-Length 형식/상한, 거짓 길이에도 스트림 총량 제한,
정확한 전체 body 상한을 확인한다. Gateway에는 직접 handler 테스트 9개를 추가해
bytes 보존, HEAD, MIME/크기 불일치와 header injection 거부, stream close와 안전한 오류를 확인했다.
이들은 실제 HTTP 통합 테스트를 대체하지 않는다. 실행된 범위에서 assertion 실패는 없었다.

현재 환경은 AF_INET socket 생성을 EPERM으로 거부하고 Unix socket bind도 거부한다.
빈 FastAPI 앱의 TestClient 진입도 멈춘다. traceback은 AnyIO thread portal과 asyncio
selector 사이에서 대기함을 보여 준다. 추가 최소 재현에서 socketpair 생성은 가능하지만
`socketpair.send()`가 EPERM으로 거부됨을 확인했다. 이 환경에서는 스레드에서 event loop로
보내는 wakeup 알림이 전달되지 않는다. 동기 endpoint를 쓰는 ASGITransport에서도 재현되지만,
async endpoint만 있는 최소 ASGI 요청은 성공했다. 이를 AutoLog 전용 장애로 간주하지 않는다.
통과시키기 위해 DB를 SQLite로 바꾸거나 auth/스토리지 검증을 끄지 않았다.
Starlette TestClient/httpx 관련 deprecation warning은 남아 있다.

```bash
# backend/, PostgreSQL을 사용하지 않는 검사에도 설정 검증을 위해 _test URL 지정
TEST_DATABASE_URL="$YOUR_LOCAL_TEST_DATABASE_URL" .venv/bin/python -m pytest -q \
  tests/test_image_validation.py tests/test_upload_guard.py \
  tests/test_uploads.py::test_sdk_adapter_uses_internal_endpoint_and_path_style
# image-gateway/
../backend/.venv/bin/python -m pytest -q tests/test_response_validation.py \
  tests/test_gateway.py::test_only_dedicated_reader_credentials
```

소켓/스레드 통신이 가능한 개발 환경에서 backend 전체 pytest, gateway 전체 pytest,
`scripts/check-image-upload-ui.py`를 다시 실행해야 한다. DB는 전용 `_test` PostgreSQL을 사용한다.

## 기존 Sites 앱과 연결할 수 있는가

Sites 관리 도구에서 기존 AutoLog 앱이 활성 상태이고 MCP 연결이 있음을 읽기 전용으로 확인했다.
현재 연결된 도구 10개는 글 CRUD·검색·목록·작성 지침·연결 확인이며 파일 입력은 없다.
`check_connection`을 실행해 `connected: true`를 확인했다. 글이나 이미지 저장은 수행하지 않았다.
이 저장소에는 Sites 서버 소스가 없으므로 정확한 기존 파일명/토큰 보관 구현은 미확인이다.
플러그인 wrapper 수정만으로 업로드가 추가된다고 주장하지 않는다.

[공식 플러그인 파일 입력 계약](https://developers.openai.com/plugins/reference#define-file-inputs)은
`openai/fileParams`로 선언한 입력에 `{download_url,file_id,mime_type?,file_name?}`를 전달한다.
네 속성을 모두 schema에 선언하되 앞의 두 개만 파일 객체의 필수 항목으로 둔다.
따라서 파일 전송 자체를 플랫폼 전체에서 불가능하다고 결론 내릴 수 없다.
하지만 문서가 현재 AutoLog Sites 앱에서 생성 이미지의 전달 성공까지 보장하지는 않는다.
현재 세션에는 해당 도구가 없어 실제 ChatGPT 생성 파일을 이 계약으로 호출할 수 없었다.

## Sites 구현 소유자가 해야 할 구체적인 변경

1. 기존 앱의 `/mcp` tools/list와 tools/call 구현을 수정한다. 별도 MCP 서버를 만들지 않는다.
   [검토용 도구 descriptor](examples/sites-upload-image.tool.json)를 사용할 수 있다.
   아직 활성화한 도구가 아니며 Action OpenAPI에도 추가하지 않았다.
2. 요청의 Sites 사용자 identity를 확인하고 기존 사용자별 AutoLog 연결 설정에 대한 권한을 검사한다.
   다른 사용자의 API 토큰을 쓰거나 모델이 API_TOKEN/업로드 destination을 지정하게 하지 않는다.
   기존 server-side API 토큰만 사용하며 Garage writer credential은 기존 backend에만 둔다.
3. 사용자 선택 파일의 실제 FileRef를 받는다. 내부 sandbox 경로, 임의 URL, base64 텍스트를
   FileRef로 간주하지 않는다. 실제 생성 이미지가 전달되지 않으면 내려받아 다시 첨부하거나
   현재 CMS의 파일 선택 경로로 전환한다. 성공한 것처럼 대체 이미지를 업로드하지 않는다.
4. FileRef download는 인증된 플랫폼 입력임을 확인하고 기본 거부 정책으로 처리한다.
   실제 관찰·검증된 정확한 HTTPS origin/path만 허용한다. URL credentials, 임의 port,
   IP literal, loopback/private/link-local/metadata 목적지와 redirects를 거부한다.
   `file_id` 문자열만으로 소유권이 증명되지는 않는다. 플랫폼 검증/파일 접근권한과 egress 정책을
   함께 확인해야 한다. DNS rebinding 방어를 보장할 수 없다면 URL fetch 경로는 활성화하지 않는다.
   이 저장소에는 외부 URL fetch endpoint를 추가하지 않았다.
5. 파일 GET에는 AutoLog Authorization/cookie를 보내지 않는다. 최대 15초 timeout과
   decompressed stream 기준 10 MiB cap을 적용하고, Content-Length 부재/위조도 처리한다.
   MIME/파일명은 힌트이며 최종 decode 검증은 기존 backend에 맡긴다.
6. 기존 고정 backend의 `POST /api/uploads`에 multipart `file` bytes를 보낸다.
   API 토큰은 이 요청에만 server-side로 첨부하고 redirect를 금지한다.
   PNG/JPEG/WebP MIME와 bytes, 201 `{url,key}`를 검증한다. upstream 상세/서명 URL/토큰을
   로그나 모델 출력에 남기지 않고 401/403/413/415/422/502/503을 구분한다.
7. 성공 시 `{url,key,markdown}`을 structuredContent와 outputSchema에 맞춰 반환한다.
   CMS와 같은 alt의 줄바꿈/역슬래시/대괄호 escape와 URL 괄호 인코딩을 적용한다.
   글 저장은 기존 update/upsert 도구로 수행하고 최신 updated_at 충돌 검사를 유지한다.
   업로드는 새 object를 만들므로 자동 재시도 시 중복 생성 가능성을 숨기지 않는다.
8. 별도 승인 후 기존 Sites 앱을 재게시해 도구 목록을 갱신한다. MCP capability와 기존
   접근 범위를 유지한다. 이번 작업에서는 재게시/원격 소스 push/Secret 변경을 하지 않았다.

대안은 기존 Sites 웹 화면에 파일 input과 인증된 same-origin multipart 전달 route를 추가하는 것이다.
브라우저 File bytes를 서버를 통해 기존 backend로 보내면 임의 source URL fetch가 필요 없다.
ChatGPT widget의 파일 선택 helper는 존재 여부를 감지해야 하며 일반 Sites 페이지에
`window.openai`가 항상 있다고 가정하면 안 된다.

완료 판정에는 실제 생성 이미지의 bytes/hash → Sites FileRef → backend 201 →
read-only gateway GET 200 → 기존 글 Markdown 저장/표시까지의 통합 검증이 필요하다.
현재 가능한 fallback은 CMS 업로드·Markdown 복사이며, 자동 전송은 **아직 미구현/미검증**이다.
