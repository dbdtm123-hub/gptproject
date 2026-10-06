# ChatGPT 대화 → Markdown 기술 블로그 Action

사용자가 평소처럼 ChatGPT와 대화한 뒤 “오늘 한 거 블로그로 정리해서 저장해”라고 요청하면
GPT가 대화의 작업·문제·원인·해결·명령·결과·배운 점을 완성된 Markdown 글로 작성한다.
AutoLog는 그 글을 검증하고 저장한다. Article API는 서버의 OpenAI 키나 호출을 사용하지 않는다.

## 연결 준비

1. 실제 외부 HTTPS 도메인과 신뢰할 수 있는 TLS 인증서를 준비한다. NodePort HTTP는 로컬 테스트용이다.
2. 루트 README 순서로 배포하고 lifelog-api Secret의 API_TOKEN을 backend/frontend 서버에 주입한다.
   create-secret-files.py는 32자 이상 무작위 토큰을 보호 파일로 생성한다. 기존 DB 비밀번호는 유지한다.
3. ACTION_SERVER_URL을 실제 HTTPS frontend URL로 설정하고 변경된 Deployment를 재시작한다.
4. GPT 편집기의 Actions에 `https://실제도메인/openapi-action.json`을 가져온다.
   또는 [actions.openapi.json](actions.openapi.json)을 붙여넣고 servers URL을 바꾼다.
5. 인증은 API Key / Bearer로 설정하고 API_TOKEN을 인증 전용 입력란에 넣는다.
   토큰을 schema, 본문, GPT 지시문이나 채팅에 적지 않는다.
6. listIngestCategories, listArticles, createArticle/upsertArticle을 테스트한다.
   공개 GPT 배포 시 플랫폼에서 요구하는 privacy policy도 설정한다.

Action schema는 Article CRUD·upsert·검색·Category·Tag를 노출한다. 기존 Entry/ingest는 호환용으로만 남아 있다.
웹 Basic 로그인과 외부 Bearer 인증을 함께 지원하며 외부 토큰을 서버 토큰으로 교체하지 않는다.
모든 웹 글은 개인용 Basic 인증으로 보호된다. published는 글 상태이고 인터넷 공개 권한을 뜻하지 않는다.

## GPT 지시문 예시

```text
평소 대화는 그대로 진행한다. 사용자가 블로그 정리/저장을 요청하면 해당 대화에서 실제 수행한
작업, 배운 개념, 명령, 오류, 원인, 해결, 결과를 추출해 완성된 기술 글을 작성한다.
대화에 없는 환경 버전, 명령 실행 성공, 결과나 원인을 확정 사실로 만들지 않는다.
필요한 추정은 추정으로 표시하고 비밀번호/API 토큰/개인 키는 본문에서 제거한다.

먼저 listIngestCategories로 기존 카테고리를 확인하고 적절하면 정확히 같은 이름을 사용한다.
listArticles(q=주제)와 필요하면 slug 검색으로 기존 글을 찾고 getArticle로 본문을 읽는다.
충분히 같은 주제면 기존 ID를 target_article_id로 보내고 기존 updated_at을 expected_updated_at으로 보낸다.
전체 글을 다시 정리하면 mode=replace와 완성된 전체 Markdown을 보낸다.
후속 작업만 이어 쓰면 mode=append와 추가 섹션을 보낸다. 새 주제는 안정적인 slug로 저장한다.
유사성 판단은 GPT의 역할이며 서버는 동일 slug 또는 명시적인 ID만 매칭한다.

title, category, subcategory, summary, content_markdown, tags를 작성한다.
source_type=chatgpt와 사용자에게 알려진 대화 참조를 source_reference로 넣는다.
대화 ID/URL을 모르면 source_reference는 null로 두고 만들지 않는다.
본문에는 구성 환경, 구축 과정, 발생한 문제, 원인, 해결, 최종 구조, 배운 점을 필요한 만큼 포함한다.
명령어는 언어가 지정된 fenced code block으로 작성한다. summary만 보내지 말고 완성된 본문을 보낸다.
새 글은 기본 draft로 저장한다. 사용자가 발행 상태를 원하면 published로 설정한다.
related_articles에는 확인된 기존 글의 UUID만 넣는다.
409이면 최신 글을 다시 읽고 사용자 의도를 유지해 재작성한다. 기존 글을 보지 않고 덮어쓰지 않는다.
삭제는 사용자가 명시적으로 요청할 때만 한다.
```

## API 계약과 upsert

| API | 역할 |
| --- | --- |
| GET /api/categories | 기존 카테고리, operationId=listIngestCategories (기존 이름 호환) |
| GET /api/tags | Article 태그 |
| GET /api/articles?q=... | 제목·요약·본문 검색, category_id/tag/status/slug 필터, 페이지 |
| GET /api/articles/{id} | 전체 Markdown, related_articles, updated_at |
| POST /api/articles | 새 글, 201. 이미 같은 slug가 있으면 409 |
| POST /api/articles/upsert | 신규 201, 기존 갱신 200 |
| PATCH /api/articles/{id} | 제공한 필드만 수정 |
| DELETE /api/articles/{id} | 글과 관련 링크 삭제, 204 |

필수 입력은 title/category/summary/content_markdown이다. slug를 생략하면 제목에서 생성한다.
주제 식별을 안정적으로 유지하려면 소문자 영문/숫자/한글과 하이픈 slug를 명시한다.
본문 최대 200,000자, 태그 최대 30개, 관련 글 최대 20개. 공백만 있는 필드와 알 수 없는 필드는 거절한다.
source_type 기본 manual, status 기본 draft, subcategory/source_reference는 null 가능하다.
related_articles는 존재하는 UUID만 허용하며 자기 자신을 참조할 수 없다.

upsert는 target_article_id가 있으면 해당 글만 갱신한다. 해당 ID가 없으면 404이고 새 글을 만들지 않는다.
ID가 없으면 같은 slug를 찾는다. 기존 글이 없으면 생성한다. 서버는 AI 유사도 판단을 하지 않는다.
replace는 content_markdown 전체를 교체한다. append는 기존 본문 + 구분선 + 새 본문을 저장하고
태그와 관련 글을 합친다. 기존 글에 생략한 선택 메타데이터는 보존하며 명시적 null/빈 목록은
replace 또는 PATCH로 지울 수 있다. 명시적 slug 변경은 주제 매칭 키를 변경한다. UI 링크는 UUID여서 유지된다.

expected_updated_at은 선택 사항이지만 기존 글 갱신에 권장한다. 불일치는 409이다.
동시 upsert는 PostgreSQL advisory/row lock으로 직렬화하고 같은 주제로 글을 중복 생성하지 않는다.
요청이 응답 전에 실패하면 transaction을 롤백하지만 응답이 불확실한 재시도는 중복 append를 만들 수 있다.
자동으로 다시 append하지 말고 최신 본문을 확인한다. 글을 교체할 때 생략한 버전 검사는 마지막 쓰기가 적용된다.
키 누락/오류는 401, 서버 인증 미설정은 503, 데이터 검증은 422이다.

## NFS 글로 acceptance test

[완성된 NFS Article 예제](examples/nfs-article.json)를 그대로 저장한다. 이 예제에는 실제 인증 값이 없다.
안전한 토큰 파일은 README의 Secret helper로 생성한 파일을 사용한다.

```bash
export AUTOLOG_URL=https://autolog.example.com
python scripts/submit-article.py --url "$AUTOLOG_URL" \
  --token-file "$SECRET_DIR/API_TOKEN" \
  --payload docs/examples/nfs-article.json --upsert
curl --fail "$AUTOLOG_URL/openapi-action.json" --output /tmp/autolog-action-schema.json
```

로컬 Compose에서는 URL을 http://127.0.0.1:3000으로 바꾼다.
반환된 id의 `/articles/ID`를 브라우저에서 열고 구성 환경·원인·해결·최종 구조·명령 코드 블록을 읽는다.
카테고리/태그 필터와 검색으로 같은 글을 찾고 수정 화면에서 Markdown 미리보기와 상태 변경을 확인한다.
본문은 HTML로 실행하지 않는다. 코드 블록은 monospace와 가로 스크롤을 지원한다.

## Secret 교체와 기존 배포

기존 DB/앱 Secret은 그대로 두고 API 토큰만 교체해야 한다면 보호된 파일에서 Secret을 갱신한다.
새 이미지의 0002 migration Job을 완료한 뒤 backend/frontend를 rollout한다. 자세한 명령은 루트 README를 따른다.
토큰 교체 시 두 Deployment를 재시작하고 Action의 인증 값도 바꾼다. 교체 중 외부 쓰기는 잠시 멈춘다.
Secret 값이나 실제 토큰 파일 내용을 출력하지 않고 안전한 비밀 저장소에 보관한다.
OpenAI Secret은 Article 기능에 필요하지 않으며 ENABLE_OPENAI_CLASSIFICATION=false를 유지해도 된다.
