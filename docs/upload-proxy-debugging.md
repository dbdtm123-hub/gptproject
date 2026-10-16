# Multipart upload proxy 오류 조사

Garage/S3/upload API와 인증은 변경하지 않았다. 이번 수정은 Next.js API proxy의 body 처리/오류 경계에 한정한다.
기존 정상 Uint8Array forwarding은 원본 multipart bytes/boundary를 보존한다. 이 방식 자체가 Cloudflare 502의 원인이라고 확정하지 않는다.
코드에서 재현한 결함은 body reader/cancel 호출이 try/catch 밖에 있어 read 오류가 handler 밖으로 전파되는 점과,
upstream HTML을 그대로 application/json 헤더로 전달하는 점이다. 기존 HEAD에서 두 회귀 테스트가 실패하고 수정본에서는 통과한다.

## 변경

- 크기를 검사하는 TransformStream을 통과한 원본 body를 ArrayBuffer로 수집한다. 수동 chunk 배열/Uint8Array 재조립은 제거한다.
- boundary가 포함된 원본 Content-Type과 bytes를 그대로 보낸다. formData 재인코딩을 하지 않는다.
- inbound Content-Length/Transfer-Encoding을 복사하지 않고 undici가 ArrayBuffer의 outgoing length를 계산한다. streaming fetch가 아니므로 duplex 옵션이 필요 없다.
- file의 10 MiB 제한은 backend가 유지한다. proxy는 multipart overhead 64 KiB를 추가한 전체 body 제한을 유지하고, JSON은 기존 1 MiB 제한을 유지한다.
- Content-Length 사전 검사와 실제 stream byte 제한을 함께 적용한다. chunked 요청도 제한된다.
- body 읽기부터 backend fetch/응답 읽기까지 outer try/catch로 보호한다. read 실패 400, 크기 초과 413, upstream 연결/비JSON 오류 502를 JSON으로 반환한다.
- 전체 요청 deadline은 75초이며 body timeout은 408, backend timeout은 504 JSON으로 반환한다.
- 관리자 cookie/외부 Bearer/Origin 검사와 login/logout 동작은 유지한다. 응답과 backend 요청에 X-Request-ID를 추가한다.
- 로그는 request ID, 단계와 byte 수만 기록한다. payload/cookie/Token/원시 fetch error를 출력하지 않는다.

## 로그로 구간 분리

```bash
kubectl -n lifelog logs deployment/lifelog-frontend --all-pods=true --prefix --since=10m
kubectl -n lifelog logs deployment/lifelog-backend --all-pods=true --prefix --since=10m
```

`upload_proxy_received`는 인증/헤더 검사를 통과해 handler가 body를 읽기 시작했음을 뜻한다.
`upload_proxy_forwarding`은 body 수집 완료와 byte 수를 뜻한다.
`api_proxy_failure`의 stage는 request_body/backend_fetch/backend_response로 오류 구간을 구분한다.
브라우저 응답 X-Request-ID와 실패 JSON request_id를 frontend 로그에 연결한다.

Cloudflare HTML 502가 계속 발생하고 해당 요청의 received 로그도 없다면 origin handler 이전의 Tunnel/Service 연결을 조사한다.
handler가 JSON을 반환해도 Cloudflare 자체의 origin 연결 오류, 프로세스 중단, 클라이언트 disconnect에는 JSON 전달을 보장할 수 없다.
운영 root cause는 변경 이미지 적용 후 실제 frontend 로그와 direct Service/Tunnel 경로 비교로 확인해야 한다.
기존 autolog.myhomlab.win Tunnel 설정을 이 수정에서 변경하지 않는다.

## 재현/검증

```bash
cd frontend
npm run typecheck
npm run build
node --test tests/upload-proxy.test.cjs
cd ..
python scripts/check-image-upload-ui.py
```

Node 테스트는 추가 의존성 없이 설치된 TypeScript/Next.js와 node:test를 사용한다.
깨진 body stream, chunked/declared oversize, byte/boundary/auth 보존, upstream HTML/reset, JSON/204, CSRF를 검사한다.
브라우저 테스트는 production Next.js와 실제 FastAPI 업로드 endpoint를 실행하고 S3 저장만 mock한다.
backend에서 실제 POST path·Content-Type·Content-Length·session/Token 검증 및 저장 직전 file SHA256을 관측한다.
1 MiB 초과 PNG 성공과 11 MiB oversize의 backend 미도달, 기존 JSON GET을 검증한다. DB write와 실제 Garage 접근은 없다.
기존 테스트 전제대로 Python Playwright/Pillow/python-dotenv, Chromium, backend/.venv, 개발 DB와 production build가 필요하다.
