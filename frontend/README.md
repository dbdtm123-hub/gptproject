# AutoLog 기술 블로그 UI

Next.js App Router / TypeScript / standalone production 이미지를 유지한다.
목록·검색·카테고리·태그·상태 필터, Markdown 상세, 작성/수정/미리보기, 관련 글, 삭제를 제공한다.
기존 Entry UI는 /legacy에 보존한다. published도 개인 웹 Basic 인증으로 보호된다.

서버 환경변수: BACKEND_URL, APP_USERNAME, APP_PASSWORD, API_TOKEN. OpenAI 키나 DB credentials를 전달하지 않는다.
웹 Basic 요청에는 서버 API_TOKEN을 주입하고, 외부 Article/태그/카테고리 Bearer는 그대로 backend에 전달한다.
공개 /openapi-action.json은 Article 중심 계약이다. 토큰은 브라우저 코드에 넣지 않는다.

react-markdown/remark-gfm은 raw HTML을 실행하지 않고 Markdown·GFM 표·코드 블록을 읽기 좋게 표시한다.
목록과 상세는 3초마다 재조회한다. 수정은 expected_updated_at으로 충돌을 차단하고 입력을 자동 덮어쓰지 않는다.

개발: .env.example을 .env.local로 복사해 서버 변수들을 채우고 npm ci / npm run dev.
검증: npm run typecheck / npm run build. production 이미지 UID 10001와 비루트 실행을 유지한다.
운영 Podman/containerd/Kubernetes와 API 제출 acceptance 예제는 루트 README를 따른다.
