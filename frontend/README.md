# AutoLog 기술 블로그 UI

Next.js App Router / TypeScript / standalone production 이미지를 유지한다.
공개 `/`와 `/articles/UUID`는 published 글의 목록·검색·카테고리 탭·태그·Markdown 상세를 제공한다.
`/login`에서 로그인한 관리자만 `/admin`과 `/admin/articles/new`, `/admin/articles/UUID/edit`를 사용한다.
이전 작성 URL과 /legacy도 서버에서 관리자 세션을 확인한다.

Frontend 서버 환경변수는 BACKEND_URL만 필요하다. API_TOKEN, APP_PASSWORD, OpenAI 키와 DB credentials를 주입하지 않는다.
로그인 응답은 서명된 세션을 HttpOnly/SameSite=Strict 쿠키로 설정하고 JSON으로 돌려주지 않는다.
HTTPS에서는 Secure 쿠키를 사용한다. Backend가 공개 읽기/관리자 읽기/쓰기 권한을 최종 검증한다.
세션 쿠키와 외부 쓰기 Bearer를 그대로 전달하며 API Token을 생성하거나 주입하지 않는다.

react-markdown/remark-gfm은 raw HTML을 실행하지 않고 Markdown·GFM 표·코드 블록을 읽기 좋게 표시한다.
목록과 상세는 3초마다 재조회한다. 수정은 expected_updated_at으로 충돌을 차단하고 입력을 자동 덮어쓰지 않는다.

개발: .env.example을 .env.local로 복사해 서버 변수들을 채우고 npm ci / npm run dev.
검증: npm run typecheck / npm run build. production 이미지 UID 10001와 비루트 실행을 유지한다.
운영 Podman/containerd/Kubernetes와 API 제출 acceptance 예제는 루트 README를 따른다.
