# 기존 AutoLog Sites에 upload_image 연결하기

현재 개발 원본과 자체 호스팅 전환은 [MCP 통합 안내](mcp-self-hosting.md)를 따릅니다. 이 문서는 이전 Sites 연결 작업의 기록이며 당시 미완료/승인 상태를 설명합니다.

상태: 로컬 연결 코드와 mock 검증 완료. 기존 Sites 서버 소스 수정/도구 등록/게시 및 실제 첨부 전송은 미실행.
운영 배포, 커밋, push, 기존 글 변경은 수행하지 않는다.

## 1. 이번 변경과 적용 대상

| 파일 | 역할 |
| --- | --- |
| `frontend/lib/sites-upload-image.ts` | FileRef 입력 검증 → 기존 인증 → 권한 확인된 파일 읽기 → 기존 전송 함수 → MCP 결과 |
| `frontend/lib/autolog-image-transport.ts` | Blob → 기존 Bearer 인증 multipart `/api/uploads` → `{url,key,markdown}` |
| `frontend/lib/image-upload.ts` | Markdown 설명 escape/HTTPS URL 검증, CMS 편집기 성공 캐시 |
| `docs/examples/sites-upload-image.tool.json` | `tools/list`에 추가할 descriptor. 이번에 `alt`도 필수로 맞춤 |
| `frontend/tests/image-upload.test.cjs` | 기존 전송/캐시 검사 6개 + Sites 연결 검사 6개 |

앞선 CMS 변경은 이미지 설명 필수, 같은 bytes의 재업로드 방지, 선택한 기존 본문 보존이다.
이번에는 **서버용** `createUploadImageTool`을 추가했다. 이 파일들을 client component에서 import하지 않는다.
기존 Sites 프로젝트로 위 TS 모듈 3개를 서버 전용 디렉터리에 함께 복사하고 상대 import를 유지한다.
추가 S3 SDK, 새로운 bucket, 이미지 저장소, backend API, 인증 체계는 필요하지 않다.

## 2. ChatGPT 첨부는 어떤 형태인가

2026-10-20 [OpenAI 공식 파일 입력 문서](https://developers.openai.com/plugins/reference#define-file-inputs)를 확인했다.
도구의 `_meta["openai/fileParams"]`에 최상위 인자 `image`를 선언하면 MCP JSON 입력에는
`download_url`, `file_id`가 들어온다. `mime_type`, `file_name`은 선택 값이다.
네 필드를 모두 schema에 선언하되 파일 객체의 required는 앞의 두 개만 둔다.
`image`와 설명 `alt` 자체를 도구 입력의 필수 항목으로 두는 것은 별도다.

예상 wire 입력 예시이며 실제 AutoLog 앱에서 캡처한 요청은 아니다:

```json
{
  "jsonrpc": "2.0",
  "id": 17,
  "method": "tools/call",
  "params": {
    "name": "upload_image",
    "arguments": {
      "image": {
        "download_url": "https://VERIFIED_PLATFORM_FILE_ORIGIN/PATH?SIGNED_PARAMETERS",
        "file_id": "PLATFORM_FILE_ID",
        "mime_type": "image/png",
        "file_name": "openstack-incident.png"
      },
      "alt": "OpenStack 장애 당시 서비스 상태"
    }
  }
}
```

바이너리/multipart가 `/mcp`로 직접 들어오는 구조가 아니다. URL의 실제 호스트/경로/서명 형식은
현재 앱에서 관찰하지 못했다. 예시 문자열을 allowlist에 넣거나 특정 CDN이라고 추측하지 않는다.
모델이 쓴 URL, `file_id`, MIME, 파일명은 파일 권한이나 이미지 유효성의 증명이 아니다.
`sandbox:/...`, `file:///...`, 모델이 작성한 base64는 이 도구의 지원 입력이 아니다.

선택한 파일이나 생성 이미지가 실제 FileRef로 전달되는지는 앱에서 검증해야 한다.
위젯이 있는 경우 파일 선택 helper는 존재 여부를 검사하고 사용할 수 있지만,
일반 Sites 페이지에 `window.openai`가 있다고 가정하지 않는다. 위젯의 API를 서버 API로 호출하지 않는다.
생성 이미지가 선택되지 않으면 사용자가 내려받아 다시 첨부하거나 CMS에서 업로드한다.

## 3. 구현된 handler와 필요한 실제 연결 두 개

`createUploadImageTool`의 필수 의존성은 다음과 같다. 둘 중 하나가 빠지면 생성 시 오류를 내므로
미완성 도구를 등록하지 않는다.

```ts
type UploadImageDependencies = {
  requireConnection(request: Request): Promise<{
    backendOrigin: string;
    publicImageBase: string;
    token: string;
  }>;
  openAuthorizedFile(
    request: Request,
    file: {
      download_url: string;
      file_id: string;
      mime_type?: string;
      file_name?: string;
    },
    signal: AbortSignal,
  ): Promise<Response>;
};
```

**이 두 이름은 이번 코드의 연결 인터페이스다. Sites가 제공한다고 확인된 내장 함수가 아니다.**

`requireConnection`은 기존 글 CRUD 도구가 사용하는 Sites 사용자 인증 및 사용자별 AutoLog 연결 설정
조회 로직을 그대로 연결한다. Sites의 인증된 사용자 ID는 Site 범위이므로 다른 앱의 ID로 대신 조회하지 않는다.
호스팅 경계가 검증한 identity만 사용하며 일반 공개 origin에서 임의로 보낸 identity header를 신뢰하지 않는다.
브라우저 세션이나 Sites 토큰을 AutoLog API 토큰 대신 보내지 않는다.
AutoLog 사용자 토큰은 서버 설정에서만 가져와 기존 Bearer 인증으로 전달한다.

`openAuthorizedFile`은 요청 사용자/현재 plugin에 허용된 파일임을 플랫폼 경로에서 확인한 뒤 bytes를 여는 연결점이다.
`file_id` prefix나 동일 사용자 로그인 여부만 검사하고 바로 URL을 fetch하는 구현은 불충분하다.
기존 앱의 실제 파일 읽기 방법과 권한 증명 방법을 확인하지 못했으므로 이 함수의 운영 구현은 제공하지 않는다.

다음은 기존 `/mcp` 초기화에 넣을 연결 예시다. `existing.*`는 **실제 소스를 확보한 후 기존 함수에
매핑해야 할 이름**이며 복사만으로 실행되는 Sites SDK 호출이 아니다.

```ts
import { createUploadImageTool } from "./server/sites-upload-image";
import { ImageTransportError } from "./server/autolog-image-transport";

// 모듈 초기화에서 한 번 생성: 요청마다 만들면 동시성 제한이 공유되지 않는다.
const uploadImage = createUploadImageTool({
  requireConnection: async request => {
    const user = await existing.requireMcpUser(request); // 기존 글 도구와 동일한 인증
    if (!user) throw new ImageTransportError(401, "Sites sign-in is required");
    const settings = await existing.getAutoLogConnection(user.id);
    if (!settings?.apiToken)
      throw new ImageTransportError(401, "Connect your AutoLog account first");
    return {
      backendOrigin: settings.backendOrigin,   // 서버 설정: https://autolog.myhomlab.win
      publicImageBase: settings.publicImageBase, // 서버 설정: https://img.myhomlab.win
      token: settings.apiToken,               // 모델/브라우저에 반환하지 않는다
    };
  },
  openAuthorizedFile: (request, fileRef, signal) =>
    existing.readPluginAuthorizedFile(request, fileRef, { signal }),
});
```

기존 설정에 `publicImageBase`가 없으면 서버의 비밀이 아닌 고정 설정으로 추가한다.
`backendOrigin`과 `token`은 기존 연결에서 재사용하며 모델 인자에서 받지 않는다.
Garage writer/reader key는 Sites로 복사하지 않는다.

## 4. 기존 /mcp tools/list와 tools/call에 추가

기존 JSON-RPC 라우터/인증/버전 협상/다른 도구 분기는 유지한다. 아래 변수명은 기존 route에 맞춰 바꾼다.
descriptor는 JSON 파일을 서버 모듈로 import하거나 동일 객체로 포함한다.
JSON import 방식은 기존 빌드 설정에 맞추고 descriptor를 두 벌로 관리하지 않는다.

```ts
import uploadImageDescriptor from "./upload-image.tool.json";

// 기존 tools/list 분기에서:
const tools = [...existingToolDescriptors, uploadImageDescriptor];
// 기존 JSON-RPC 응답 작성 코드가 result: { tools }를 반환하도록 한다.

// 기존 tools/call 분기에서, 기존 route 인증 이후:
if (rpc.params.name === "upload_image") {
  const result = await uploadImage(request, rpc.params.arguments);
  // 기존 JSON-RPC serializer에 이 result를 전달한다.
  return Response.json({ jsonrpc: "2.0", id: rpc.id, result });
}
// 다른 name은 기존 CRUD 도구 처리로 계속 보낸다.
```

기존 MCP SDK를 쓰는 앱이면 SDK의 기존 tool 등록 방식으로 같은 descriptor와 handler를 연결한다.
다른 `/mcp` 서버나 OAuth 앱을 새로 만들지 않는다. 기존 hosting manifest의 `mcp` capability,
프로젝트 ID, plugin ID, audience, 인증 범위를 유지한다.

파일 reader가 연결되지 않았다면 descriptor 추가와 분기 활성화를 둘 다 보류한다.
기능 플래그를 쓴다면 서버 설정에서만 활성화하고, list와 call 양쪽에 같은 플래그를 적용한다.
권한이 없는 사용자는 실제 handler에서도 거부하므로 도구 목록 숨김만으로 인증을 대신하지 않는다.

성공 결과:

```json
{
  "content": [{"type": "text", "text": "{\"url\":\"...\",\"key\":\"...\",\"markdown\":\"...\"}"}],
  "structuredContent": {
    "url": "https://img.myhomlab.win/uploads/YYYY/MM/UUID.png",
    "key": "uploads/YYYY/MM/UUID.png",
    "markdown": "![OpenStack 장애 당시 서비스 상태](https://img.myhomlab.win/uploads/YYYY/MM/UUID.png)"
  },
  "isError": false
}
```

오류는 `isError: true`와 `content` 안의 `{error:{status,message}}`로 반환한다.
실패 결과에 성공용 `structuredContent`나 임시/추측 URL을 넣지 않는다. JSON-RPC transport 오류와
정상 호출 중 업로드 실패를 혼동하지 않는다. 원본 파일 URL/서명/토큰/upstream body를 로그에 넣지 않는다.

## 5. 파일 다운로드 보안의 미완료 경계

현재 코드는 URL 문법만 검사하며 임의 URL을 서버에서 fetch하지 않는다.
**따라서 현재 코드에 SSRF 방어가 완성된 downloader가 있다고 설명하면 안 된다.**

우선 기존 플랫폼의 인증된 파일 reader가 있는지 확인한다. 이것이 없다면 직접 다운로드를 활성화하기 전에
실제 요청을 통해 확인된 정확한 HTTPS host/path만 허용하고 다음을 구현·검증해야 한다.

- 현재 caller/plugin의 파일 접근 권한 확인. signed URL을 임의 입력과 구분할 플랫폼 검증 경로 확보.
- 기본 거부 egress, IP literal/내부/loopback/link-local/metadata 목적지 차단.
- 실제 연결 목적지의 공인 IP 검증 및 DNS rebinding 방어. DNS 사전 조회 후 일반 fetch만 하는 것으로 대체하지 않음.
- 모든 redirect 금지. 원본 URL의 query 서명은 파일 source에만 전달.
- source GET에 AutoLog Authorization/Cookie를 보내지 않음. 고정 backend POST에만 Bearer 주입.
- 15초 제한과 AbortSignal을 원격 요청 **및 응답 stream 읽기 전체**에 적용.
- 압축 해제 후 bytes 10 MiB 상한. Content-Length가 없거나 거짓이어도 실제 stream 제한 적용.

Sites Worker의 일반 fetch에서 연결 IP 고정/검증이 불가능하다면 검증된 platform reader나
동등한 egress 보장 없이 다운로드를 활성화하지 않는다. 이 문서는 새 프록시 서비스 도입을 지시하지 않는다.

구현된 handler는 source 응답의 declared size/실제 stream을 모두 제한하고, 200만 허용한다.
처음 bytes로 지원 포맷의 MIME 힌트를 정한다. FileRef의 선택 MIME/파일명은 신뢰하지 않는다.
유효 이미지 여부는 기존 backend의 Pillow가 최종 판정한다. header만 맞는 가짜 PNG도 backend에서 거절된다.
빈 파일/지원 외 형식/SVG는 거절한다. reader가 signal을 무시하면 deadline 보장이 불가능하므로
해당 adapter를 운영에 연결해서는 안 된다.

한 isolate에서 업로드 하나만 허용해 파일 읽기/복사/Blob/FormData 비용을 제한한다.
여러 isolate/프로세스/사용자에 걸친 rate limit과 영속 중복 방지는 기존 앱 측 정책으로 적용해야 한다.
handler 자체는 자동 재시도를 하지 않는다. CMS의 성공 캐시는 이 MCP handler에 적용되지 않는다.
도구 호출을 다시 실행하면 새 UUID object가 생길 수 있으므로 성공 결과를 다음 글 저장에 재사용한다.

## 6. 인증·Garage·글 저장 흐름

```text
기존 Sites 인증 및 사용자 연결 조회
  → 권한 확인된 ChatGPT FileRef 읽기
  → Blob (최대 10 MiB)
  → 기존 POST /api/uploads (Bearer, multipart file)
  → 기존 Pillow 검증 → 기존 Garage writer → UUID key
  → 201 url/key 검증 → markdown 반환
  → 기존 create/update/upsert 도구로 글 저장
```

upload handler는 글을 자동 저장하지 않는다. 기존 글 도구의 작성 지침/중복 주제 검색/
카테고리 재사용과 `expected_updated_at` 검사를 유지한다.
이미지 실패 시 본문에 깨진 링크를 넣지 않는다. 이미지 없이 저장할 때는 실패 사실을 사용자에게 알린다.
글 저장만 실패하면 성공한 URL을 유지하고 저장 실패를 보고한다. 재업로드해서 해결하려 하지 않는다.

## 7. 접근 상태와 적용 절차

| 구분 | 확인 상태 |
| --- | --- |
| 저장소 `work` 수정/로컬 테스트 | 가능, 기존 미커밋 변경 보존 |
| 공식 FileRef 계약 확인 | 완료 |
| 기존 AutoLog Sites 메타데이터 | 앞선 작업에서 owner/active 및 MCP 연결 확인 |
| Sites 접근 권한 거부 | 확인된 사실 아님. 권한 부족으로 단정하지 않음 |
| 기존 Sites 서버 소스 | 앞선 source fetch에서 `git.chatgpt-team.site` DNS 해석 실패. 현재 미확보 |
| 실제 사용자별 토큰 저장·파일 권한 검증 함수 | 소스/런타임 접근이 없어 확인·매핑 불가 |
| 실제 ChatGPT 첨부/생성 이미지 → 이 도구 | 아직 미호출. 현재 설치된 도구 목록에는 upload_image 없음 |
| Garage 및 외부 표시 통합 | 미검증. 로컬 mock 통과로 대체하지 않음 |

이번에는 원격 credential 재발급, 환경변수/권한 변경이나 원격 게시를 시도하지 않았다.

적용 담당자가 진행할 순서:

1. 네트워크 가능한 개발 환경에서 **기존** Sites 소스를 열고 기존 글 도구의 인증/connection 조회와
   `/mcp` 라우터를 찾는다. 새 프로젝트 생성이나 기존 audience 변경은 하지 않는다.
2. 파일 권한 확인과 실제 bytes 접근 방법을 확인한다. 기존 runtime에 reader가 없다면 위 보안 경계를
   충족하는 다운로드 방법을 먼저 입증한다. signed URL 원문을 공개 로그나 문서에 남기지 않는다.
3. 서버 전용 모듈 3개와 descriptor를 가져오고 두 의존성을 실제 코드에 연결한다.
   기존 연결의 backend origin/API token을 재사용한다. reader 없이 도구를 활성화하지 않는다.
4. 기존 로컬 MCP 테스트에서 list의 descriptor와 call의 입력/출력/오류를 확인하고 기존 CRUD 회귀를 실행한다.
5. 별도 게시 승인 전까지 로컬 상태로 둔다. **현재 요청에서는 이 단계에서 멈춘다.**
6. 추후 승인된 기존 Sites 릴리스 절차로 게시한 뒤 도구 목록 갱신을 확인한다. frontend 배포만으로
   MCP 도구가 생기는 것은 아니다. 별도 plugin/App을 만들지 않는다.
7. 테스트용 글에서 실제 첨부 PNG 및 실제 생성 이미지를 각각 호출하고 source hash → Garage key →
   내부/외부 GET·HEAD 200/MIME/hash → Markdown 저장 → 공개 페이지 표시를 확인한다.
   파일 권한 거부/만료/413/잘못된 이미지/S3 장애, 없는 key 404, gateway 비허용 method도 확인한다.
   운영 기존 글 이전은 별도 승인 이후에만 진행한다.

## 8. 이번 로컬 검증

```bash
cd frontend
node tests/image-upload.test.cjs
npm run typecheck
```

새 연결 테스트를 포함한 위 suite는 **12 passed**다. 이번 추가 6개는 FileRef 선택 필드 부재,
기존 multipart 전달, 필수 host 연결 누락, 인증 선검사/입력 거부, declared/chunked 크기 제한,
안전한 오류 및 동시성 slot 해제를 확인한다. 기존 전송/캐시 6개도 함께 재실행했다.
mock reader와 mock backend를 사용했으며 실제 ChatGPT·Sites 파일 읽기·Garage의 성공 증거가 아니다.
이전의 42개 전체를 이번에 다시 실행했다는 의미도 아니다.

전체 변경·이전 검증 경계는 [분석 보고서](autolog-image-upload-review.md), 이미지 이전은 해당 문서의
RabbitMQ 절차를 참조한다.
