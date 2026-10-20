# 인증 서버 작업 검증 기록

2026-10-20, 기준 commit `0e1048760a071b24aa0d64a65e163299e13521ba`.
운영 배포·Secret 변경·Sites 변경은 수행하지 않았다.

| 검증 | 결과 | 증거 범위 |
| --- | --- | --- |
| 기존 MCP + 이미지 guard 테스트 | 67 passed | 외부 I/O mock/ASGI 회귀 테스트. 실제 OAuth 아님 |
| OAuth acceptance verifier 경계 테스트 | 20 passed | 잘못된 claims/issuer/endpoint/scope 및 배포 설정 거부 |
| Python compile, shell syntax, diff whitespace | 통과 | 문법/정적 검사 |
| Authelia 4.39.28 source/공식 문서 | 검토 | 기능/설정의 후보 근거. 실행 증거 아님 |
| 실제 Authelia lab 실행 시도 | 미실행 | `Required tool missing: docker`로 종료. Podman도 없음 |
| release example 렌더 시도 | 예제 digest 거부 확인 | 실제 kubectl/Kustomize 렌더는 도구 부재로 미검증 |
| 실제 운영 상태 조사 | 미완료 | kubectl/kubeconfig 없음, 외부 DNS 실패 |
| 실제 MFA/ChatGPT 연결 | 미실행 | 운영 미배포이며 ChatGPT UI 연결 gate 미충족 |
| 원본 저장소 commit 준비 시도 | 실패 | `.git/index.lock`: Read-only file system |
| GitHub tree 생성 | 거부 | `MCP tool call requires approval, but approval policy is never` |

실제 OAuth/배포 성공으로 주장하지 않는다. Authelia 최종 채택은 `mcp-auth-homelab.md`의 A~D gate 후 결정한다.
배포 artifact 검토는 실제 운영 정책과 이미지 digest, 필요한 Secret 참조, StorageClass가 확인된 뒤 마친다.
현재 파일들은 검증 후보 구현이며 운영 적용용 승인본이 아니다.

원본 Git 메타데이터를 수정하지 않고 별도 로컬 delivery clone의 `work`에 변경을 커밋해 bundle/format-patch로 인계한다. 이 커밋은 GitHub에 게시된 커밋이 아니다.
