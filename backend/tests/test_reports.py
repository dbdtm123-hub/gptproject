"""Private report lifecycle, immutable history, and unchanged article boundaries."""
import os
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from pydantic import SecretStr
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.main import app
from app.config import get_settings


@pytest.fixture
def report():
    return dict(report_id="OPENSTACK-RABBITMQ-0020", title="RabbitMQ measured improvement", project="OpenStack",
                component="RabbitMQ", author="Lead SRE", source="Codex", status="PARTIAL", summary="One stream repaired",
                findings="Missing local replica", changes="Added two replicas on one stream",
                environment="Three controllers", raw_markdown="# Private report\nMeasured operations, not publication.",
                metrics_before=[dict(name="A CPU", value=55.69, unit="%", window_start="2026-11-12T11:39:15Z", window_end="2026-11-12T11:44:15Z")],
                metrics_after=[dict(name="A CPU", value=49.16, unit="%", observed_at="2026-11-12T11:51:05Z")],
                remaining_issues="Oversized consumer service queue", next_actions="Separate approval")


def create(client, report):
    response = client.post("/api/reports", json=report)
    assert response.status_code == 200, response.text
    return response.json()


def replacement(report, revision, **changes):
    return {k: v for k, v in {**report, **changes, "expected_revision": revision}.items() if k != "report_id"}


def test_report_create_upsert_conflict_and_history(client, report):
    first = create(client, report)
    assert first["revision"] == 1
    assert create(client, report) == first  # uncertain create can safely replay identical input
    assert client.post("/api/reports", json={**report, "summary": "different report"}).status_code == 409
    changed = client.patch("/api/reports/" + report["report_id"], json=replacement(report, 1, summary="Verified result"))
    assert changed.status_code == 200, changed.text
    current = changed.json()
    assert current["revision"] == 2 and current["created_at"] == first["created_at"]
    assert current["updated_at"] >= first["updated_at"]
    assert client.patch("/api/reports/" + report["report_id"], json=replacement(report, 1)).status_code == 409
    assert client.post("/api/reports", json={**report, "project": "Other", "expected_revision": 2}).status_code == 409
    history = client.get(f"/api/reports/{report['report_id']}/history").json()
    assert [h["revision"] for h in history] == [2, 1]
    assert history[1]["snapshot"]["summary"] == report["summary"]
    assert history[0]["actor"] == "owner-api-token"
    assert client.get(f"/api/reports/{report['report_id']}/history?limit=1&offset=1").json()[0]["revision"] == 1
    # CAS upsert also updates in place; no duplicate row, version or creation-time replacement.
    upsert = client.post("/api/reports", json={**report, "summary": "Final", "expected_revision": 2})
    assert upsert.json()["revision"] == 3
    assert client.get("/api/reports").json()["total"] == 1


def test_filter_search_sort_pagination_latest_and_current_issues(client, report):
    create(client, report)
    newer = {**report, "report_id": "OPENSTACK-RABBITMQ-0021", "title": "Followup", "status": "SUCCESS", "remaining_issues": ""}
    create(client, newer)
    create(client, {**report, "report_id": "KUBERNETES-ETCD-0001", "project": "Kubernetes", "component": "etcd", "status": "BLOCKED"})
    for params in [dict(project="OpenStack", component="RabbitMQ", status="PARTIAL"), dict(q="local replica"),
                   dict(report_id=report["report_id"]), dict(q="publication"), dict(q="55.69")]:
        result = client.get("/api/reports", params=params)
        assert result.status_code == 200, result.text
        assert report["report_id"] in [r["report_id"] for r in result.json()["items"]]
    assert client.get("/api/reports?q=%25%25%25").json()["total"] == 0  # literal LIKE metacharacters
    assert client.get("/api/reports", params={"date_to": "2020-01-01T00:00:00Z"}).json()["total"] == 0
    assert client.get("/api/reports", params={"date_from": "2020-01-01T00:00:00Z", "date_to": "2019-01-01T00:00:00Z"}).status_code == 422
    result = client.get("/api/reports?sort=oldest&limit=1&offset=1").json()
    assert result["total"] == 3 and result["items"][0]["report_id"] == report["report_id"]
    latest = client.get("/api/reports/latest?project=OpenStack").json()
    assert latest["report_id"] == newer["report_id"]
    assert client.get("/api/reports/latest?project=Missing").status_code == 404
    issues = client.get("/api/reports/unresolved").json()
    assert issues["total"] == 1 and issues["items"][0]["project"] == "Kubernetes"
    assert client.get("/api/reports/projects").json() == {"projects": ["Kubernetes", "OpenStack"], "components": ["RabbitMQ", "etcd"]}
    assert client.get("/api/reports?limit=101").status_code == 422
    assert client.get("/api/reports?offset=-1").status_code == 422


def test_private_access_scoped_credentials_and_cookie_csrf(client, report, monkeypatch):
    create(client, report)
    settings = get_settings()
    monkeypatch.setattr(settings, "report_read_token", SecretStr("r" * 40))
    monkeypatch.setattr(settings, "report_write_token", SecretStr("w" * 40))
    with TestClient(app) as anonymous:
        for path in ["", "/projects", "/latest?project=OpenStack", "/unresolved", "/" + report["report_id"], "/" + report["report_id"] + "/history"]:
            response = anonymous.get("/api/reports" + path)
            assert response.status_code == 401
            assert response.headers["cache-control"] == "private, no-store"
        assert anonymous.post("/api/reports", json=report).status_code == 401
        read = {"Authorization": "Bearer " + "r" * 40}
        assert anonymous.get("/api/reports", headers=read).status_code == 200
        assert anonymous.post("/api/reports", headers=read, json=report).status_code == 401
        assert anonymous.post("/api/articles", headers=read, json={"title":"Denied", "category":"Private", "summary":"Denied", "content_markdown":"Denied"}).status_code == 401
        write = {"Authorization": "Bearer " + "w" * 40}
        assert anonymous.post("/api/reports", headers=write, json=report).status_code == 200
        assert anonymous.get("/api/reports", headers={"Authorization": "Bearer bad"}).status_code == 401
    # Explicit invalid bearer cannot fall back to a valid owner cookie.
    assert client.get("/api/reports", headers={"Authorization": "Bearer bad"}).status_code == 401
    client.headers.pop("authorization")
    assert client.get("/api/reports").status_code == 200
    assert client.post("/api/reports", json=report, headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.post("/api/reports", json=report, headers={"Origin": "http://localhost:3000"}).status_code == 200


def test_reports_never_enter_blog_feed_and_drafts_stay_private(client, report):
    article = client.post("/api/articles", json=dict(title="Keep draft", category="OpenStack", summary="Original", content_markdown="Original draft")).json()
    report["related_article_ids"] = [article["id"]]
    create(client, report)
    assert client.get("/api/articles/" + article["id"]).json()["content_markdown"] == "Original draft"
    with TestClient(app) as public:
        response = public.get("/api/articles")
        assert report["report_id"] not in response.text and response.json()["total"] == 0
        assert public.get("/api/articles/" + article["id"]).status_code == 401
        assert public.get("/api/articles/" + article["id"], headers={"Authorization": "Bearer " + os.environ["API_TOKEN"]}).status_code == 401
    assert client.delete("/api/reports/" + report["report_id"]).status_code == 405


def test_identity_history_and_measurement_validation(client, report, db_session):
    first = create(client, report)
    for sql in ["UPDATE report_revisions SET actor = 'changed'", "DELETE FROM report_revisions",
                "UPDATE operation_reports SET created_at = now() + interval '1 day'"]:
        with pytest.raises(DBAPIError):
            with db_session.begin_nested():
                db_session.execute(text(sql))
    assert client.get("/api/reports/" + report["report_id"]).json()["created_at"] == first["created_at"]
    for changes in [dict(created_at="2000-01-01T00:00:00Z"), dict(status="published"), dict(report_id="bad"),
                    dict(related_report_ids=[report["report_id"]]), dict(related_report_ids=["MISSING-REPORT-0001"]),
                    dict(metrics_after=[dict(name="CPU", value=1, unit="%", window_start="2026-01-01T00:00:00Z")])]:
        assert client.post("/api/reports", json={**report, "expected_revision": 1, **changes}).status_code == 422
    missing = {**report, "report_id": "MISSING-REPORT-0002", "metrics_after": [{"name": "temperature", "value": None, "unit": "°C", "note": "not measured"}]}
    assert create(client, missing)["metrics_after"][0]["value"] is None
