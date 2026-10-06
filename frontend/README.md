# Autolog frontend

Next.js App Router / TypeScript / standalone production 이미지.
자연어 입력, AI 분류 결과 편집·일괄 저장, 직접 분류, 타임라인, 카테고리 필터, 상세·삭제를 제공한다.

서버 환경변수: BACKEND_URL, APP_USERNAME, APP_PASSWORD.
OpenAI 키와 DB credentials를 이 서비스에 전달하지 않는다. 브라우저는 같은 origin의 /api를 호출하고
Next.js가 내부 backend로 프록시한다. 비밀번호가 없으면 보호된 경로는 503으로 닫힌다.
Basic 인증은 HTTPS에서 운영한다. NodePort HTTP는 제한된 접속 IP의 테스트용이다.

개발은 .env.example을 .env.local로 복사하고 실제 로컬 값으로 채운 뒤 npm ci / npm run dev를 사용한다.
production 검증은 npm run typecheck / npm run build를 사용한다.
Dockerfile은 node:22-bookworm-slim 기반 다단계 빌드이며 UID 10001로 실행한다.
health/live는 프로세스, health/ready는 로그인 설정과 backend readiness를 확인한다.

Podman build/push와 Kubernetes 실행은 저장소 루트 README를 따른다.
