"""Build through the cloud's existing HTTPS proxy and trusted CA bundle.

No credentials or certificate files are copied into the checkout or final image.
Normal local development should use `docker compose build` instead.
"""
import os
from pathlib import Path
import socket
import ssl
import subprocess
from urllib.parse import urlsplit


def main() -> None:
    env = os.environ.copy()
    env.setdefault("DOCKER_CONFIG", "/tmp/lifelog-docker")
    proxy = urlsplit(env.get("HTTPS_PROXY", ""))
    if not proxy.hostname:
        raise SystemExit("HTTPS_PROXY is required for the cloud build helper")
    env["CLOUD_PROXY_HOST"] = proxy.hostname
    env["CLOUD_PROXY_IP"] = socket.gethostbyname(proxy.hostname)
    ca_path = env.get("SSL_CERT_FILE") or ssl.get_default_verify_paths().cafile
    if not ca_path or not Path(ca_path).is_file():
        raise SystemExit("A trusted CA bundle is required; set SSL_CERT_FILE")
    env["CLOUD_CA_BUNDLE"] = ca_path
    subprocess.run(
        [
            "docker", "compose", "-f", "docker-compose.yml", "-f", "docker-compose.cloud.yml",
            "--progress", "plain", "build", "--build-arg", "HTTPS_PROXY",
            "--build-arg", "HTTP_PROXY", "--build-arg", "NO_PROXY",
        ],
        cwd=Path(__file__).resolve().parents[1], env=env, check=True,
    )


if __name__ == "__main__":
    main()
