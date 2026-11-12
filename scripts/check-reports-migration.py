"""Verify additive migration, conflicting creates, backup/restore, and guarded rollback locally.
Requires two fresh databases ending in _test on localhost. Never reads production credentials.
"""
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "backend"))
source = os.environ["TEST_DATABASE_URL"]
restore = os.environ["RESTORE_TEST_DATABASE_URL"]
for url in [source, restore]:
    parsed = make_url(url)
    if parsed.host not in {"localhost", "127.0.0.1"} or not parsed.database.endswith("_test"):
        raise RuntimeError("Local disposable _test databases only")
if source == restore: raise RuntimeError("Restore target must be separate")
os.environ.update(DATABASE_URL=source, API_TOKEN=secrets.token_urlsafe(40), APP_PASSWORD=secrets.token_urlsafe(32))
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from app.main import app
cfg = Config(str(root / "backend/alembic.ini")); cfg.set_main_option("script_location", str(root / "backend/alembic"))
command.upgrade(cfg, "0002")
engine = create_engine(source)
category, article = uuid4(), uuid4()
with engine.begin() as c:
    c.execute(text("INSERT INTO categories(id,name) VALUES (:id,'Preserved category')"), {"id": category})
    c.execute(text("""INSERT INTO articles(id,title,slug,category_id,summary,content_markdown,tags,source_type,status)
                     VALUES (:id,'Preserved draft','preserved-draft',:cat,'Keep','Exact original content','[]','manual','draft')"""), {"id":article,"cat":category})

def fingerprint():
    with engine.connect() as c:
        return c.execute(text("SELECT row_to_json(a)::text FROM articles a ORDER BY id")).scalars().all()
before = fingerprint()
artifacts = root / ".deploy"; artifacts.mkdir(exist_ok=True)
def dump(url, path):
    p = make_url(url); env = os.environ.copy()
    if p.password: env["PGPASSWORD"] = p.password
    subprocess.run(["pg_dump", "-h", p.host, "-p", str(p.port or 5432), "-U", p.username, "-d", p.database, "-Fc", "-f", str(path)], env=env, check=True, capture_output=True)
    path.chmod(0o600)
dump(source, artifacts / "reports-before-migration.dump")
command.upgrade(cfg, "head"); command.check(cfg)
assert fingerprint() == before
payload = dict(report_id="DEVELOPMENT-CONCURRENCY-0001",title="Concurrency check",project="Development",component="Concurrency",author="Test",source="Manual",status="SUCCESS",summary="first",raw_markdown="Test fixture")
def post(summary):
    with TestClient(app) as client:
        return client.post("/api/reports", headers={"Authorization":"Bearer " + os.environ["API_TOKEN"]}, json={**payload,"summary":summary}).status_code
with concurrent.futures.ThreadPoolExecutor(2) as pool:
    statuses = sorted(pool.map(post, ["first", "second"]))
assert statuses == [200,409], statuses
with engine.connect() as c:
    assert c.scalar(text("SELECT count(*) FROM operation_reports")) == 1
    assert c.scalar(text("SELECT count(*) FROM report_revisions")) == 1
try:
    command.downgrade(cfg, "0002")
except RuntimeError as error:
    assert "Reports exist" in str(error)
else: raise AssertionError("Populated downgrade must refuse data loss")
assert fingerprint() == before
dump(source, artifacts / "reports-after-migration.dump")
p = make_url(restore); env = os.environ.copy()
if p.password: env["PGPASSWORD"] = p.password
subprocess.run(["pg_restore", "-h", p.host, "-p", str(p.port or 5432), "-U", p.username, "-d", p.database,
                "--no-owner", "--no-acl", "--exit-on-error", str(artifacts / "reports-after-migration.dump")], env=env, check=True, capture_output=True)
restored = create_engine(restore)
with restored.connect() as c:
    assert c.execute(text("SELECT row_to_json(a)::text FROM articles a ORDER BY id")).scalars().all() == before
    assert c.scalar(text("SELECT count(*) FROM operation_reports")) == 1
    assert c.scalar(text("SELECT count(*) FROM report_revisions")) == 1
    assert c.scalar(text("SELECT version_num FROM alembic_version")) == "0003"
result = {"migration":"0002->0003", "existing_article_unchanged":True, "concurrent_create_statuses":statuses,
          "populated_downgrade_refused":True, "backup_restore_verified":True,
          "article_snapshot_sha256":hashlib.sha256(json.dumps(before).encode()).hexdigest()}
(artifacts / "reports-migration-verification.json").write_text(json.dumps(result, indent=2));print(json.dumps(result))
restored.dispose();engine.dispose()
