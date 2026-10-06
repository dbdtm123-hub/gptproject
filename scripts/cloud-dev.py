"""Cloud development/test lifecycle only; production uses Podman and Kubernetes.

Preserve configured login credentials. If none exist, use a private temporary test
password, never a production password or invented OpenAI credential.
Run with backend/.venv/bin/python after dependency installation.
"""
import argparse
import base64
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
from urllib.request import Request, build_opener, ProxyHandler

from dotenv import dotenv_values

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("action", choices=["build", "start"])
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
values = dotenv_values(root / ".env")
env = os.environ.copy()
env.setdefault("DOCKER_CONFIG", "/tmp/lifelog-docker")
password = env.get("APP_PASSWORD") or values.get("APP_PASSWORD")
if not password:
    path = Path("/tmp/autolog-test-password")
    if not path.exists():
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "w") as file:
            file.write(secrets.token_urlsafe(32))
    password = path.read_text()
env["APP_PASSWORD"] = password
if args.action == "build":
    subprocess.run([sys.executable, str(root / "scripts/cloud-build.py")], cwd=root, env=env, check=True)
else:
    subprocess.run(["docker", "compose", "up", "-d", "--wait", "--wait-timeout", "120"], cwd=root, env=env, check=True)
    opener = build_opener(ProxyHandler({}))
    with opener.open("http://127.0.0.1:3000/health/ready", timeout=10) as response:
        assert json.load(response)["status"] == "ready"
    username = env.get("APP_USERNAME") or values.get("APP_USERNAME") or "autolog"
    authorization = "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()
    request = Request("http://127.0.0.1:3000/api/entries", headers={"Authorization": authorization})
    with opener.open(request, timeout=10) as response:
        assert {"items", "total", "limit", "offset"}.issubset(json.load(response))
    print("Development frontend, authenticated API proxy and DB readiness verified.")
