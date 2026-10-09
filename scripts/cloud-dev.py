"""Cloud development/test lifecycle only; production uses Podman and Kubernetes.

Preserve configured login credentials. If none exist, use a private temporary test
password, never a production password or invented OpenAI credential.
Run with backend/.venv/bin/python after dependency installation.
"""
import argparse
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
token = env.get("API_TOKEN") or values.get("API_TOKEN")
if not token:
    path = Path("/tmp/autolog-test-api-token")
    if not path.exists():
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "w") as file:
            file.write(secrets.token_urlsafe(48))
    token = path.read_text()
env["API_TOKEN"] = token
if args.action == "build":
    subprocess.run([sys.executable, str(root / "scripts/cloud-build.py")], cwd=root, env=env, check=True)
else:
    subprocess.run(["docker", "compose", "up", "-d", "--wait", "--wait-timeout", "120"], cwd=root, env=env, check=True)
    opener = build_opener(ProxyHandler({}))
    with opener.open("http://127.0.0.1:3000/health/ready", timeout=10) as response:
        assert json.load(response)["status"] == "ready"
    request = Request("http://127.0.0.1:3000/api/articles")
    with opener.open(request, timeout=10) as response:
        page = json.load(response)
        assert {"items", "total", "limit", "offset"}.issubset(page)
        assert all(item["status"] == "published" for item in page["items"])
    print("Public blog, published-only API proxy and DB readiness verified.")
