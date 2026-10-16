# Kubernetes 운영 매니페스트

Podman 빌드 → GHCR → Kubernetes/containerd. CNI는 Cilium, 기본 외부 접속은 frontend NodePort 30080이다.
기존 namespace/resource 이름 lifelog를 유지한다. [전체 배포 순서](../README.md)를 따른다.

| 단계 | 디렉터리 | 리소스 |
| --- | --- | --- |
| 01 | platform | Namespace, ServiceAccount, ConfigMap |
| 별도 | examples | Secret placeholder, 선택적 local PV/StorageClass |
| 02 | database | PostgreSQL StatefulSet, headless Service, 기본 PVC |
| 03 | migration | backend와 같은 이미지의 Alembic Job |
| 04 | backend | Deployment, ClusterIP Service, probes, PDB |
| 05 | frontend | Deployment, NodePort Service, probes, PDB |
| 06 | networking | NetworkPolicy, NodePort/Ingress 및 OpenAI용 CiliumNetworkPolicy |
| 07 (선택) | ingress | Cilium Ingress, TLS, frontend 경유 인증 |

기본 이미지는 autolog/backend:latest와 autolog/frontend:latest이며 imagePullPolicy는 Always다.
scripts/render-k8s.py로 ghcr.io/사용자 prefix를 적용하고 순서대로 apply한다.
기본 PVC는 StorageClass를 생략한다. --storage-class로 지정하거나,
--database-storage emptydir를 명시해 데이터 유실을 허용하는 테스트를 할 수 있다.
StorageClass가 전혀 없으면 examples/local-storage.yaml.example을 실제 노드에 맞춰 사용한다.
frontend의 --frontend-service ClusterIP 옵션은 TLS Ingress 전환용이다.

OpenAI Secret은 backend에만 주입한다. 미설정 시 수동 기록만 가능하다.
전체 출력 디렉터리나 placeholder Secret을 일괄 apply하지 않는다.
단일 PostgreSQL은 HA가 아니며 영속 데이터 백업과 별도 복원 검증이 필요하다.
