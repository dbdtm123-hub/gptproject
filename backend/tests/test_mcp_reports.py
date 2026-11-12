"""Existing MCP wire -> real private Reports API; no public publication/cookie involved."""
import os

import httpx
from pydantic import SecretStr

from app import mcp_tools
from app.main import app
from test_mcp import environment, rpc, send  # reuse the existing OAuth/transport harness
from test_reports import report


def test_report_tools_full_stack_shared_owner_and_scopes(client, report, environment, monkeypatch):
    settings, claims = environment
    settings.backend_token = SecretStr(os.environ["API_TOKEN"])
    settings.allowed_subjects = ["codex-owner", "chatgpt-owner"]
    settings.subject_scopes = {name: ["autolog:reports:read", "autolog:reports:write"] for name in settings.allowed_subjects}
    claims.update(sub="codex-owner", scope="autolog:reports:read autolog:reports:write")
    real_client = httpx.AsyncClient
    def factory(**kwargs):
        if kwargs.get("timeout") == 30:
            return real_client(transport=httpx.ASGITransport(app=app), timeout=30, follow_redirects=False)
        return real_client(**kwargs)
    monkeypatch.setattr(mcp_tools.httpx, "AsyncClient", factory)
    def call(name, args):
        response = send(rpc("tools/call", {"name": name, "arguments": args}))
        assert response.status_code == 200, response.text
        result = response.json()["result"]
        assert not result["isError"], result
        return result["structuredContent"]
    created = call("create_report", report)
    assert created["report_id"] == report["report_id"] and created["revision"] == 1
    # Both hosts' authorized OAuth subjects reach the same single-owner report store.
    claims["sub"] = "chatgpt-owner"
    assert call("get_report", {"report_id": report["report_id"]})["raw_markdown"] == report["raw_markdown"]
    assert call("create_report", report)["revision"] == 1
    for tool in ["search_reports", "list_reports"]:
        assert call(tool, {"project": "OpenStack", "component": "RabbitMQ", "q": "replica", "status": "PARTIAL", "limit": 1})["total"] == 1
    assert call("get_latest_project_report", {"project": "OpenStack"})["report_id"] == report["report_id"]
    assert call("get_unresolved_issues", {"project": "OpenStack"})["items"][0]["remaining_issues"]
    assert call("update_report", {**report, "expected_revision": 1, "summary": "Updated via MCP"})["revision"] == 2
    assert call("get_report_history", {"report_id": report["report_id"]})["items"][1]["revision"] == 1
    conflict = send(rpc("tools/call", {"name": "update_report", "arguments": {**report, "expected_revision": 1}})).json()["result"]
    assert conflict["isError"] and "409" in conflict["content"][0]["text"]
    claims["scope"] = "autolog:reports:read"
    assert send(rpc("tools/call", {"name": "create_report", "arguments": report})).status_code == 403
    assert send(rpc("tools/call", {"name": "create_article", "arguments": {}})).status_code == 403
    claims["scope"] = "autolog:read autolog:write"
    assert send(rpc("tools/call", {"name": "get_report", "arguments": {"report_id": report["report_id"]}})).status_code == 403
    claims["sub"] = "other-user"
    assert send(rpc()).status_code == 401


def test_reports_advertise_narrow_scopes(environment):
    contracts = {tool["name"]: tool for tool in mcp_tools.tools()}
    for name in ["get_report", "search_reports", "list_reports", "get_latest_project_report", "get_unresolved_issues"]:
        assert contracts[name]["securitySchemes"][0]["scopes"] == ["autolog:reports:read"]
        assert contracts[name]["annotations"]["readOnlyHint"]
    for name in ["create_report", "update_report"]:
        assert contracts[name]["securitySchemes"][0]["scopes"] == ["autolog:reports:write"]
        assert not contracts[name]["annotations"]["readOnlyHint"]
