# 인증 서버 작업 검증 기록

## 추가 실증: GitHub Actions Authelia 4.39.28 OAuth + MCP

- 검증 코드 commit: `bdac4cdc76cb3200322a239f9c456b7c815010c0`, 브랜치 `work`.
- [실제 OAuth 호환성 랩](https://github.com/dbdtm123-hub/gptproject/actions/runs/39492803707) — **success**, 로그의 `REAL_AUTHELIA_OAUTH_PASS` 확인.
- [전체 AutoLog CI + 세 이미지 발행](https://github.com/dbdtm123-hub/gptproject/actions/runs/39492803682) — **success**, Backend·Frontend·Image Gateway·매니페스트 렌더 및 GHCR 발행 작업 통과.
- 별도 Docker에 띄운 실제 Authelia로 로그인·동의·Code+PKCE S256·발급·Introspection·MCP initialize·11개 tools/list·Refresh/Revoke·토큰 만료까지 검사.
- 잘못된 PKCE, code 재사용, 다른 resource/audience, 다른 사용자, 불충분한 scope, refresh token을 MCP bearer로 사용하는 공격 시나리오를 거부함.
- Authelia 4.39.28의 introspection은 access/refresh 양쪽에 `active:true`를 반환하고 `token_type`을 생략했다. 기본 MCP는 여전히 엄격한 RFC7662 타입 검증을 유지하고, Authelia v4를 실증한 release에서만 `authelia_opaque_v4` opt-in으로 `authelia_at_` 형식을 추가 확인하며 `authelia_rt_`를 거부한다.
- 격리된 1-factor 테스트 사용자로 검증했으며 **실제 운영 배포, MFA, 공개 HTTPS, ChatGPT 플러그인 연결, Garage 파일 업로드는 미검증**.
- Authelia 실험 컨테이너의 단일 시점 샘플: CPU 0.02%, 메모리 66.14MiB (idle 측정/용량 보증 아님).
- **운영 사전 블로커**: 기존 Kubernetes 기본 StorageClass가 NFS였다면, `autolog-auth-data` PVC의 `storageClassName`을 SQLite의 파일 잠금과 일관성에 적합한 로컬/블록 스토리지로 별도 지정해야 함. 실제 StorageClass/PV, Tunnel, Secret, image digest, DNS 및 ChatGPT callback 확인 전 배포 금지.

## 초기 Codex 개발 환경 검증 기록 (당시 환경 제약)

아래 표는 위 실증 이전의 2026-10-20 오프라인 평가다. 당시의 미실행/실패는 현재 GitHub Actions 실증 결과로 보완되었으며, 실제 운영 배포 여부는 여전히 미검증이다.


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
