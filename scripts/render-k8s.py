"""Render an ordered production release without modifying checked-in manifests.

Requires kubectl with Kustomize support. Supports registry-qualified tags or digests.
No cluster connection or credentials are used by this command.
"""
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

STAGES = ("platform", "database", "migration", "backend", "frontend", "networking", "ingress")


def image_override(name: str, image: str) -> dict[str, str]:
    if re.fullmatch(r"[a-z0-9.-]+(?::[0-9]+)?/[a-z0-9/._-]+@sha256:[a-f0-9]{64}", image):
        repository, digest = image.rsplit("@", 1)
        return {"name": name, "newName": repository, "digest": digest}
    if re.fullmatch(r"[a-z0-9.-]+(?::[0-9]+)?/[a-z0-9/._-]+:[A-Za-z0-9_.-]+", image):
        repository, tag = image.rsplit(":", 1)
        return {"name": name, "newName": repository, "newTag": tag}
    raise ValueError("Use a registry-qualified image with a tag or sha256 digest")


def patch(kind: str, name: str, changes: list[dict]) -> dict:
    return {"target": {"kind": kind, "name": name}, "patch": json.dumps(changes)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", default="ghcr.io/your-github-user", help="Registry and owner prefix")
    parser.add_argument("--image", "--backend-image", dest="image", help="Backend tag/digest (also used by migration)")
    parser.add_argument("--frontend-image", help="Frontend tag/digest")
    parser.add_argument("--postgres-image", default="docker.io/library/postgres:17-bookworm")
    parser.add_argument("--database-storage", choices=["pvc", "emptydir"], default="pvc")
    parser.add_argument("--storage-class", help="Omit to use default StorageClass; local PV example: autolog-local")
    parser.add_argument("--storage-size", default="20Gi")
    parser.add_argument("--hostname", required=True, help="TLS hostname, also used for CORS")
    parser.add_argument("--frontend-service", choices=["NodePort", "ClusterIP"], default="NodePort")
    parser.add_argument("--node-port", type=int, default=30080)
    parser.add_argument("--output", required=True, type=Path, help="New output directory; existing paths are refused")
    args = parser.parse_args()
    try:
        images = [image_override("autolog/backend", args.image or f"{args.registry}/autolog/backend:latest"),
                  image_override("autolog/frontend", args.frontend_image or f"{args.registry}/autolog/frontend:latest"),
                  image_override("postgres", args.postgres_image)]
    except ValueError as error:
        parser.error(str(error))
    if not re.fullmatch(r"[a-z0-9]([a-z0-9.-]*[a-z0-9])?", args.hostname) or "." not in args.hostname:
        parser.error("--hostname must be a DNS hostname without scheme or port")
    if args.storage_class and not re.fullmatch(r"[a-z0-9]([a-z0-9.-]*[a-z0-9])?", args.storage_class):
        parser.error("--storage-class must be a valid Kubernetes name")
    if not re.fullmatch(r"[1-9][0-9]*(Mi|Gi|Ti)", args.storage_size):
        parser.error("--storage-size must be a positive Mi/Gi/Ti quantity")
    if not 30000 <= args.node_port <= 32767:
        parser.error("--node-port must be within the default Kubernetes range 30000..32767")
    if args.output.exists():
        parser.error("--output already exists; choose a new release directory to preserve previous releases")
    root = Path(__file__).resolve().parents[1]
    rendered = {}
    with tempfile.TemporaryDirectory(prefix="lifelog-kustomize-") as temp:
        for stage in STAGES:
            overlay = Path(temp) / stage
            overlay.mkdir()
            shutil.copytree(root / "k8s" / stage, overlay / "base")
            config = {"apiVersion": "kustomize.config.k8s.io/v1beta1", "kind": "Kustomization",
                      "resources": ["base"], "images": images}
            if stage == "database":
                if args.database_storage == "emptydir":
                    changes = [
                        {"op": "remove", "path": "/spec/volumeClaimTemplates"},
                        {"op": "remove", "path": "/spec/persistentVolumeClaimRetentionPolicy"},
                        {"op": "add", "path": "/spec/template/spec/volumes/-", "value": {
                            "name": "data", "emptyDir": {"sizeLimit": args.storage_size}}},
                    ]
                else:
                    changes = [{"op": "replace", "path": "/spec/volumeClaimTemplates/0/spec/resources/requests/storage", "value": args.storage_size}]
                    if args.storage_class:
                        changes.append({"op": "add", "path": "/spec/volumeClaimTemplates/0/spec/storageClassName", "value": args.storage_class})
                config["patches"] = [patch("StatefulSet", "lifelog-postgres", changes)]
            elif stage == "frontend":
                changes = [{"op": "replace", "path": "/spec/type", "value": args.frontend_service}]
                if args.frontend_service == "ClusterIP":
                    changes.extend([{"op": "remove", "path": "/spec/ports/0/nodePort"},
                                    {"op": "remove", "path": "/spec/externalTrafficPolicy"}])
                else:
                    changes.append({"op": "replace", "path": "/spec/ports/0/nodePort", "value": args.node_port})
                config["patches"] = [patch("Service", "lifelog-frontend", changes)]
            elif stage == "platform":
                config["patches"] = [patch("ConfigMap", "lifelog-config", [
                    {"op": "replace", "path": "/data/CORS_ORIGINS", "value": json.dumps([f"https://{args.hostname}"])},
                ])]
            elif stage == "ingress":
                config["patches"] = [patch("Ingress", "lifelog", [
                    {"op": "replace", "path": "/spec/rules/0/host", "value": args.hostname},
                    {"op": "replace", "path": "/spec/tls/0/hosts/0", "value": args.hostname},
                ])]
            (overlay / "kustomization.yaml").write_text(json.dumps(config), encoding="utf-8")
            try:
                rendered[stage] = subprocess.run(
                    ["kubectl", "kustomize", str(overlay)], check=True, capture_output=True, text=True,
                ).stdout
            except subprocess.CalledProcessError as error:
                raise SystemExit(f"Kustomize failed for {stage}:\n{error.stderr}") from None
    # Only write after every stage rendered successfully. Never include Secret values.
    args.output.mkdir(parents=True)
    for index, (stage, contents) in enumerate(rendered.items(), start=1):
        (args.output / f"{index:02d}-{stage}.yaml").write_text(contents, encoding="utf-8")
    print(f"Rendered {len(rendered)} stages to {args.output}")
    if args.database_storage == "emptydir":
        print("TEST ONLY: emptyDir loses PostgreSQL data when the DB Pod is removed or rescheduled.")


if __name__ == "__main__":
    main()
