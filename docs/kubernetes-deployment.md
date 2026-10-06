# Kubernetes 운영 참고

현재 전체 배포 순서는 [루트 README](../README.md)에 있다.
기본은 GHCR, Cilium CNI, frontend NodePort이며 Cinder와 Cilium Ingress 설치를 요구하지 않는다.
PostgreSQL 기본은 PVC, StorageClass 없는 환경은 local PV 예시 또는 명시적 emptyDir 테스트 옵션을 사용한다.

## containerd 사설 레지스트리

GHCR은 공인 인증서이므로 보통 CA 변경이 필요 없다. 사설 레지스트리를 추가한다면 모든 pull 대상 노드의
/etc/containerd/certs.d/REGISTRY[:PORT]/에 신뢰 CA와 hosts.toml을 설치한다.
기존 설정을 조사하고 병합하며 config 전체를 예시로 덮어쓰지 않는다.

~~~toml
# /etc/containerd/certs.d/registry.example.com/hosts.toml
server = "https://registry.example.com"
[host."https://registry.example.com"]
  capabilities = ["pull", "resolve"]
  ca = "/etc/containerd/certs.d/registry.example.com/ca.crt"
~~~

containerd 1.x / config v2:

~~~toml
[plugins."io.containerd.grpc.v1.cri".registry]
  config_path = "/etc/containerd/certs.d"
~~~

containerd 2.x / config v3:

~~~toml
[plugins."io.containerd.cri.v1.images".registry]
  config_path = "/etc/containerd/certs.d"
~~~

CRI 활성화 및 kubelet/containerd의 cgroup 설정은 기존 정상 클러스터 설정을 유지한다.
config_path 변경에 필요한 containerd 재시작은 노드 유지보수 절차에 따른다.
skip_verify 또는 HTTP registry로 TLS를 우회하지 않는다. Pod 인증은 imagePullSecret을 사용한다.
Podman은 /etc/containers/certs.d 또는 rootless 사용자 certs.d의 CA를 사용한다.
Docker daemon은 Kubernetes 노드에서 실행하지 않는다.

## 영속 데이터와 업데이트

StatefulSet의 volumeClaimTemplate와 StorageClass는 초기 배포 전에 선택한다.
기존 템플릿 변경은 보통 immutable이다. PVC 확장은 해당 StorageClass의 allowVolumeExpansion을 확인한 뒤
기존 PVC에 대해 별도로 수행한다. PVC→emptyDir 전환을 기존 운영 StatefulSet에 직접 apply하지 않는다.
Retain은 데이터 백업을 대체하지 않는다. PVC 자체를 삭제하면 PV reclaim policy에 따라 실제 데이터가 삭제될 수 있다.
local PV는 특정 노드에 고정된다. HA가 필요하면 외부 DB 또는 전용 PostgreSQL operator로 이전한다.

새 앱 배포 전 DB를 백업하고 migration의 이전 버전 호환성을 확인한다.
Job은 고정 이름이므로 이전 실행이 종료된 것을 확인하고 이전 Job만 삭제한 뒤 새 Job을 실행한다.
동시에 여러 migration을 실행하지 않는다. API/DB Secret 변경은 기존 DB 암호를 자동 변경하지 않는다.
latest image 또는 ConfigMap/Secret 변경은 API/UI rollout restart로 반영한다.
rollout undo는 DB를 되돌리지 않는다. 자동 downgrade 대신 별도 복구 계획과 검증된 백업을 사용한다.
