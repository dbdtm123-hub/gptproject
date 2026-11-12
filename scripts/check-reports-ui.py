"""Browser integration against local PostgreSQL _test DB, API, proxy and production Next build.
Creates only isolated test records. Optional ARTICLE_COPY_JSON tests one explicit offline copy.
Requires Playwright/Chromium and the existing backend/frontend dependencies.
"""
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
from uuid import UUID

import httpx
from playwright.sync_api import sync_playwright, expect
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "backend"))
from app.models import Article, Category
from importlib.util import module_from_spec, spec_from_file_location
spec = spec_from_file_location("copy_report", root / "scripts/prepare-report-copy.py")
copy_module = module_from_spec(spec); spec.loader.exec_module(copy_module)
url = os.environ.get("TEST_DATABASE_URL", "")
parsed = make_url(url)
if not (parsed.database or "").endswith("_test") or parsed.host not in {"127.0.0.1", "localhost"}:
    raise RuntimeError("Requires a local isolated PostgreSQL database ending in _test")
password, token = secrets.token_urlsafe(32), secrets.token_urlsafe(40)
env = {**os.environ, "DATABASE_URL": url, "APP_USERNAME": "reports-test", "APP_PASSWORD": password,
       "API_TOKEN": token, "BACKEND_URL": "http://127.0.0.1:8120"}
backend = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8120", "--no-access-log"], cwd=root / "backend", env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
front_env = {k: v for k, v in env.items() if k not in {"DATABASE_URL", "API_TOKEN", "APP_PASSWORD", "APP_USERNAME", "REPORT_READ_TOKEN", "REPORT_WRITE_TOKEN"}}
frontend = subprocess.Popen(["node", "node_modules/next/dist/bin/next", "start", "--hostname", "127.0.0.1", "--port", "3120"], cwd=root / "frontend", env=front_env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    for base in ["http://127.0.0.1:8120/health/ready", "http://127.0.0.1:3120"]:
        for _ in range(80):
            try:
                if httpx.get(base).status_code == 200: break
            except httpx.HTTPError: pass
            time.sleep(.2)
        else: raise RuntimeError("Local application did not start")
    headers = {"Authorization": "Bearer " + token}
    report = dict(report_id="DEVELOPMENT-BROWSER-0001", title="개발 검증 리포트", project="Development", component="Browser",
                  author="Test", source="Manual", status="PARTIAL", summary="Development fixture only", findings="Read-only diagnostics",
                  changes="Test-only change", raw_markdown="# 원문 검증\n<script>window.untrusted=true</script>\n**Rendered safely**",
                  metrics_before=[dict(name="Test metric", value=10, unit="%")], metrics_after=[dict(name="Test metric", value=8, unit="%")],
                  remaining_issues="Test fixture outstanding issue", next_actions="Test fixture next action")
    response = httpx.post("http://127.0.0.1:8120/api/reports", headers=headers, json=report); response.raise_for_status()
    copied = None
    if os.environ.get("ARTICLE_COPY_JSON"):
        from datetime import datetime
        original = json.loads(Path(os.environ["ARTICLE_COPY_JSON"]).read_text())
        engine = create_engine(url)
        with Session(engine) as session:
            if session.get(Category, UUID(original["category_id"])) is None:
                session.add(Category(id=UUID(original["category_id"]), name="OpenStack")); session.flush()
            fields = {k: v for k, v in original.items() if k in Article.__table__.columns.keys()}
            for k in ["id", "category_id"]: fields[k] = UUID(fields[k])
            for k in ["created_at", "updated_at"]: fields[k] = datetime.fromisoformat(fields[k])
            if session.get(Article, UUID(original["id"])) is None:
                session.add(Article(**fields))
            session.commit()
        copied = copy_module.prepare(original, "OPENSTACK-RABBITMQ-0020", "OpenStack", "RabbitMQ", "Codex", "Codex", "PARTIAL")
        saved = httpx.post("http://127.0.0.1:8120/api/reports", headers=headers, json=copied); saved.raise_for_status()
        fetched = httpx.get("http://127.0.0.1:8120/api/reports/OPENSTACK-RABBITMQ-0020", headers=headers); fetched.raise_for_status()
        assert fetched.json()["raw_markdown"] == original["content_markdown"]
        assert hashlib.sha256(fetched.json()["raw_markdown"].encode()).digest() == hashlib.sha256(original["content_markdown"].encode()).digest()
        with Session(engine) as session:
            article = session.get(Article, UUID(original["id"]))
            assert article.status == "draft" and article.content_markdown == original["content_markdown"]
        (root / ".deploy/report20-copy-verification.json").write_text(json.dumps({"report_id": copied["report_id"], "markdown_sha256": hashlib.sha256(original["content_markdown"].encode()).hexdigest(), "original_draft_unchanged": True, "target": "development-only"}, indent=2))
        engine.dispose()
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH", "/usr/bin/chromium-browser"), args=["--no-sandbox"])
        guest = browser.new_context(); page = guest.new_page(); errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto("http://127.0.0.1:3120/reports"); expect(page).to_have_url("http://127.0.0.1:3120/login")
        assert guest.request.get("http://127.0.0.1:3120/api/reports").status == 401
        page.get_by_label("사용자 이름").fill("reports-test"); page.get_by_label("비밀번호", exact=True).fill(password)
        page.get_by_role("button", name="로그인", exact=True).click(); expect(page).to_have_url("http://127.0.0.1:3120/admin")
        page.get_by_role("link", name="운영 리포트", exact=True).click(); expect(page.get_by_role("heading", name="운영 리포트", exact=True)).to_be_visible()
        page.get_by_label("프로젝트", exact=True).select_option("Development")
        expect(page.locator(".article-grid .article-card")).to_have_count(1)
        page.locator(".article-grid").get_by_role("link", name="개발 검증 리포트", exact=True).click()
        expect(page.get_by_role("heading", name="전후 측정 비교")).to_be_visible()
        expect(page.locator("tbody")).to_contain_text("-2 %")
        expect(page.get_by_role("heading", name="남은 문제", exact=True)).to_be_visible()
        page.get_by_text("원문 Markdown", exact=True).click(); expect(page.get_by_text("Rendered safely")).to_be_visible()
        assert page.evaluate("window.untrusted") is None
        page.get_by_role("button", name="이력 보기", exact=True).click(); expect(page.get_by_text("버전 1 ·", exact=False)).to_be_visible()
        page.get_by_role("link", name="리포트 수정", exact=True).click()
        page.get_by_label("요약", exact=True).fill("Updated in the browser")
        page.get_by_role("button", name="비공개 리포트 저장", exact=True).click()
        expect(page).to_have_url("http://127.0.0.1:3120/reports/DEVELOPMENT-BROWSER-0001")
        expect(page.get_by_text("Updated in the browser", exact=True)).to_be_visible()
        page.goto("http://127.0.0.1:3120/reports/new")
        for label, value in {"리포트 ID":"DEVELOPMENT-BROWSER-0002", "제목":"UI created report", "프로젝트":"Development", "컴포넌트":"UI", "요약":"UI create", "원문 Markdown":"# UI create"}.items():
            page.get_by_label(label, exact=True).fill(value)
        page.get_by_role("button", name="비공개 리포트 저장", exact=True).click()
        expect(page).to_have_url("http://127.0.0.1:3120/reports/DEVELOPMENT-BROWSER-0002")
        page.goto("http://127.0.0.1:3120/reports"); page.screenshot(path=str(root / ".deploy/reports-dashboard.png"), full_page=True)
        mobile = browser.new_context(viewport={"width":390,"height":844})
        mobile.add_cookies(guest.cookies()); mp = mobile.new_page(); mp.goto("http://127.0.0.1:3120/reports")
        expect(mp.get_by_role("heading", name="운영 리포트", exact=True)).to_be_visible()
        assert mp.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        mp.screenshot(path=str(root / ".deploy/reports-mobile.png"), full_page=True)
        public = browser.new_context(); public_page = public.new_page(); public_page.goto("http://127.0.0.1:3120")
        expect(public_page.get_by_role("heading", name="AutoLog", exact=True)).to_be_visible()
        assert "개발 검증 리포트" not in public_page.locator("body").inner_text()
        assert "UI created report" not in public_page.locator("body").inner_text()
        assert not errors, errors
        browser.close()
    print("PASS: private login, dashboard/filter, detail/metrics, safe Markdown, history, edit/create, mobile, public-feed separation; explicit copy=" + str(bool(copied)))
finally:
    for process in [frontend, backend]:
        process.terminate()
        try: process.wait(timeout=10)
        except subprocess.TimeoutExpired: process.kill(); process.wait()
