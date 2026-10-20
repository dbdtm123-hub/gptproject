"""MCP wire/auth/adapters over async ASGI; mock external I/O, no DB/socket."""
import asyncio
from io import BytesIO
import json
import time
from unittest.mock import Mock

import httpx
from fastapi import HTTPException
from PIL import Image
import pytest

from app import mcp_auth, mcp_files, mcp_server, mcp_tools
from app.mcp_config import MCPSettings


@pytest.fixture
def environment(monkeypatch):
    s = MCPSettings(resource="https://mcp.example.com/mcp", issuer="https://id.example.com", backend_token="backend-test-secret",
                    introspection_client_id="test-client", introspection_client_secret="idp-test-secret", allowed_subjects=["owner"],
                    file_sources={"files.example.com": ["/selected/"]})
    for module in [mcp_auth, mcp_server, mcp_tools]:
        monkeypatch.setattr(module, "get_mcp_settings", lambda: s)
    mcp_tools.image_cache.clear()
    claims = {"active": True, "iss": s.issuer, "aud": s.resource, "exp": time.time() + 300,
              "sub": "owner", "scope": "autolog:read autolog:write"}
    async def introspect(token):
        assert token == "oauth-token"
        return claims
    monkeypatch.setattr(mcp_auth, "introspect", introspect)
    return s, claims


def rpc(method="tools/list", params=None, **changes):
    return {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}, **changes}


def send(payload=None, headers=None, method="POST", path="/mcp", content=None):
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=mcp_server.app), base_url="https://mcp.example.com") as client:
            merged = {"Authorization": "Bearer oauth-token", "Accept": "application/json, text/event-stream", **(headers or {})}
            return await client.request(method, path, headers=merged, **({"content": content} if content is not None else {"json": payload}))
    return asyncio.run(run())


def test_discovery_initialize_notifications_and_stateless_transport(environment):
    meta = send(method="GET", path="/.well-known/oauth-protected-resource").json()
    assert meta["resource"] == environment[0].resource
    result = send(rpc("initialize", {"protocolVersion": "future-version", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}))
    assert result.json()["result"]["protocolVersion"] == "2025-06-18"
    assert "mcp-session-id" not in result.headers
    notification = rpc("notifications/initialized"); notification.pop("id")
    assert send(notification).status_code == 202
    assert send(method="GET").status_code == 405
    assert send(rpc("ping")).json()["result"] == {}


def test_tools_and_exact_fileref_contract(environment):
    listed = send(rpc()).json()["result"]["tools"]
    assert {tool["name"] for tool in listed} == set(mcp_tools.CONTRACTS)
    upload = next(tool for tool in listed if tool["name"] == "upload_image")
    image = upload["inputSchema"]["properties"]["image"]
    assert image["required"] == ["download_url", "file_id"]
    assert set(image["properties"]) == {"download_url", "file_id", "mime_type", "file_name"}
    assert upload["_meta"]["openai/fileParams"] == ["image"]
    assert upload["securitySchemes"][0]["scopes"] == ["autolog:write"]
    assert next(t for t in listed if t["name"] == "delete_article")["annotations"]["destructiveHint"]


@pytest.mark.parametrize("change", [
    {"active": False}, {"iss": "https://other.example.com"}, {"aud": "other-resource"}, {"exp": 0},
    {"sub": "other-user"}, {"exp": True}, {"scope": None}, {"aud": None},
])
def test_oauth_rejects_bad_claims(environment, change):
    environment[1].update(change)
    result = send(rpc())
    assert result.status_code == 401
    assert "resource_metadata=" in result.headers["www-authenticate"]


def test_auth_missing_and_insufficient_scope(environment):
    assert send(rpc(), headers={"Authorization": ""}).status_code == 401
    environment[1]["scope"] = "autolog:read"
    denied = send(rpc("tools/call", {"name": "create_article", "arguments": {}}))
    assert denied.status_code == 403 and "insufficient_scope" in denied.headers["www-authenticate"]


@pytest.mark.parametrize("headers,status", [
    ({"Origin": "https://evil.example"}, 403), ({"Host": "evil.example"}, 403),
    ({"Accept": "application/json"}, 406), ({"MCP-Protocol-Version": "future"}, 400),
    ({"Content-Type": "text/plain"}, 415),
])
def test_transport_guards(environment, headers, status):
    assert send(rpc(), headers=headers).status_code == status


def test_rpc_parse_size_and_notification_safety(environment, monkeypatch):
    assert send(content=b'{bad', headers={"Content-Type": "application/json"}).json()["error"]["code"] == -32700
    assert send(content=b'x' * (1024 * 1024 + 1), headers={"Content-Type": "application/json"}).status_code == 413
    assert send([rpc()]).json()["error"]["code"] == -32600
    assert send(rpc("unknown")).json()["error"]["code"] == -32601
    assert send(rpc("tools/call", {"name": "unknown"})).json()["error"]["code"] == -32602
    notification = rpc("tools/call", {"name": "delete_article", "arguments": {"article_id": "00000000-0000-4000-8000-000000000000"}})
    notification.pop("id")
    monkeypatch.setattr(mcp_server, "call_tool", Mock(side_effect=AssertionError("must not execute")))
    assert send(notification).status_code == 202


def test_crud_preserves_versions_and_no_conflict_retry(environment, monkeypatch):
    seen = []
    async def backend(method, path, **kwargs):
        seen.append((method, path, kwargs))
        if method == "PATCH": raise HTTPException(409, "Article changed; read it again before updating")
        return {"id": "00000000-0000-4000-8000-000000000000", "status": "draft"}
    monkeypatch.setattr(mcp_tools, "backend", backend)
    content = {"title": "제목", "category": "기술", "summary": "요약", "content_markdown": "기존 본문\n추가"}
    assert not send(rpc("tools/call", {"name": "create_article", "arguments": content})).json()["result"]["isError"]
    assert seen[-1][0:2] == ("POST", "/api/articles")
    assert seen[-1][2]["json"]["content_markdown"] == content["content_markdown"]
    args = {"article_id": "00000000-0000-4000-8000-000000000000", "content_markdown": "보존된 본문", "expected_updated_at": "2026-10-20T00:00:00Z"}
    error = send(rpc("tools/call", {"name": "update_article", "arguments": args})).json()["result"]
    assert error["isError"] and '409' in error["content"][0]["text"]
    assert sum(method == "PATCH" for method, _, _ in seen) == 1
    assert seen[-1][2]["json"]["expected_updated_at"] == args["expected_updated_at"]
    assert "article_id" not in seen[-1][2]["json"]


def test_private_reads_explicit_configuration(environment, monkeypatch):
    paths = []
    async def backend(method, path, **kwargs): paths.append(path); return {"items": []}
    monkeypatch.setattr(mcp_tools, "backend", backend)
    args = rpc("tools/call", {"name": "search_articles", "arguments": {"status": "draft"}})
    send(args); assert paths[-1] == "/api/articles"
    environment[0].private_reads = True
    send(args); assert paths[-1] == "/api/integrations/articles"


def test_image_bytes_success_cache_and_markdown(environment, monkeypatch):
    output = BytesIO(); Image.new("RGB", (2, 2), "green").save(output, format="PNG"); data = output.getvalue()
    async def in_thread(fn, *args): return fn(*args)
    monkeypatch.setattr(mcp_tools, "run_in_threadpool", in_thread)
    monkeypatch.setattr(mcp_tools, "download_image", lambda *args: data)
    key = "uploads/2026/10/00000000000040008000000000000000.png"; calls = []
    async def backend(method, path, **kwargs):
        calls.append((method, path)); assert kwargs["files"]["file"][1] == data
        return {"url": environment[0].image_public_base + "/" + key, "key": key}
    monkeypatch.setattr(mcp_tools, "backend", backend)
    args = {"image": {"download_url": "https://files.example.com/selected/image?signature=private", "file_id": "hint"}, "alt": "상태 [화면]"}
    first = send(rpc("tools/call", {"name": "upload_image", "arguments": args})).json()["result"]
    second = send(rpc("tools/call", {"name": "upload_image", "arguments": args})).json()["result"]
    assert not first["isError"] and first["structuredContent"] == second["structuredContent"]
    assert r"\[화면\]" in first["structuredContent"]["markdown"] and len(calls) == 1
    assert "signature=private" not in json.dumps(first)


@pytest.mark.parametrize("url", ["http://files.example.com/selected/a", "https://127.0.0.1/selected/a", "https://[::1]/selected/a",
    "https://other.example.com/selected/a", "https://files.example.com/other/a", "https://files.example.com/selected/../a",
    "https://files.example.com/selected/%252e%252e/a", "https://user:password@files.example.com/selected/a",
    "https://files.example.com:8443/selected/a", "https://files.example.com/selected/a#bad"])
def test_file_source_policy(url):
    with pytest.raises(HTTPException): mcp_files.source_target(url, {"files.example.com": ["/selected/"]})


@pytest.mark.parametrize("ips", [["127.0.0.1"], ["169.254.169.254"], ["10.0.0.1"], ["::1"], ["8.8.8.8", "192.168.0.1"]])
def test_dns_blocks_private_and_mixed(monkeypatch, ips):
    monkeypatch.setattr(mcp_files.socket, "getaddrinfo", lambda *a, **kw: [(None, None, None, None, (ip, 443)) for ip in ips])
    with pytest.raises(HTTPException) as error: mcp_files.resolve_public("files.example.com")
    assert error.value.status_code == 403


def test_tls_pin_keeps_original_sni(monkeypatch):
    raw = Mock(); context = Mock(); monkeypatch.setattr(mcp_files.ssl, "create_default_context", lambda: context)
    create = Mock(return_value=raw); monkeypatch.setattr(mcp_files.socket, "create_connection", create)
    conn = mcp_files.PinnedHTTPS("files.example.com", "8.8.8.8"); conn.connect()
    assert create.call_args.args[0] == ("8.8.8.8", 443)
    assert context.wrap_socket.call_args.kwargs["server_hostname"] == "files.example.com"


@pytest.mark.parametrize("status,headers,data,expected", [
    (302, {}, b"", 502), (200, {"Content-Length": "10485761"}, b"", 413),
    (200, {"Content-Encoding": "gzip"}, b"", 415), (200, {}, b"x" * (10 * 1024 * 1024 + 1), 413),
    (200, {}, b"", 422),
])
def test_download_limits(monkeypatch, status, headers, data, expected):
    response = Mock(status=status); response.getheader.side_effect = lambda name, default=None: headers.get(name, default)
    body = BytesIO(data); response.read.side_effect = body.read
    conn = Mock(); conn.getresponse.return_value = response
    monkeypatch.setattr(mcp_files, "resolve_public", lambda host: "8.8.8.8")
    monkeypatch.setattr(mcp_files, "PinnedHTTPS", lambda *args: conn)
    with pytest.raises(HTTPException) as error:
        mcp_files.download_image("https://files.example.com/selected/a?signature=private", {"files.example.com": ["/selected/"]})
    assert error.value.status_code == expected
    conn.close.assert_called_once()
    headers_sent = conn.request.call_args.kwargs["headers"]
    assert "Authorization" not in headers_sent and "Cookie" not in headers_sent


def test_private_read_gate_and_core_query_reuse(monkeypatch):
    from app import mcp_read_api
    from app.articles import query_articles
    settings = Mock(mcp_private_reads_enabled=False)
    monkeypatch.setattr(mcp_read_api, 'get_settings', lambda: settings)
    with pytest.raises(HTTPException) as error: mcp_read_api.require_private_reads()
    assert error.value.status_code == 503
    settings.mcp_private_reads_enabled = True
    mcp_read_api.require_private_reads()
    session = Mock(); session.scalar.return_value = 0; session.scalars.return_value.all.return_value = []
    with pytest.raises(HTTPException) as error: query_articles(session, False, status='draft')
    assert error.value.status_code == 401
    assert query_articles(session, True, status='draft').total == 0
    for route in mcp_read_api.router.routes:
        assert {dep.call for dep in route.dependant.dependencies} >= {mcp_read_api.require_private_reads, mcp_read_api.require_api_token}


def test_mcp_backend_adapter_sends_only_existing_token_and_no_redirects(environment, monkeypatch):
    real_client = httpx.AsyncClient
    async def run():
        observed = []
        async def respond(request):
            observed.append(request)
            return httpx.Response(200, json={'status': 'ok'})
        client = real_client(transport=httpx.MockTransport(respond))
        def factory(**kwargs):
            assert kwargs['follow_redirects'] is False
            return client
        monkeypatch.setattr(mcp_tools.httpx, 'AsyncClient', factory)
        assert await mcp_tools.backend('GET', '/health/ready') == {'status': 'ok'}
        assert observed[0].headers['authorization'] == 'Bearer backend-test-secret'
        assert observed[0].url == environment[0].backend_url + '/health/ready'
        assert 'oauth-token' not in str(observed[0].headers)
    asyncio.run(run())


def test_introspection_uses_separate_credentials_and_bounded_fixed_endpoint(environment, monkeypatch):
    real_client = httpx.AsyncClient
    async def run():
        async def respond(request):
            assert str(request.url) == environment[0].introspection_url
            assert request.headers['authorization'].startswith('Basic ')
            assert 'backend-test-secret' not in str(request.headers)
            assert b'token=oauth-token' in request.content
            return httpx.Response(200, json={'active': True})
        client = real_client(transport=httpx.MockTransport(respond))
        monkeypatch.setattr(mcp_auth.httpx, 'AsyncClient', lambda **kwargs: client)
        # environment fixture replaced introspect: call the real implementation explicitly.
        from importlib.util import module_from_spec, spec_from_file_location
        from pathlib import Path
        spec = spec_from_file_location('real_mcp_auth_test', Path(mcp_auth.__file__))
        module = module_from_spec(spec); spec.loader.exec_module(module)
        module.get_mcp_settings = lambda: environment[0]
        assert await module.introspect('oauth-token') == {'active': True}
    asyncio.run(run())
