"""Standalone, stateless MCP Streamable HTTP resource server. No startup mutation."""
import asyncio
import json
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError

from app.mcp_auth import READ, WRITE, authenticate, require_scope
from app.mcp_config import get_mcp_settings
from app.mcp_tools import CONTRACTS, call_tool, tools

app = FastAPI(title="AutoLog MCP", docs_url=None, redoc_url=None, openapi_url=None)
VERSIONS = ["2025-06-18", "2025-03-26"]
MAX_RPC_BYTES = 1024 * 1024
upload_busy = False


@app.get("/health/live")
async def live():
    return {"status": "ok"}


@app.get("/health/ready")
async def ready():
    s = get_mcp_settings()
    if not s.allowed_subjects or not s.introspection_client_id or not s.introspection_client_secret.get_secret_value() or not s.backend_token.get_secret_value():
        raise HTTPException(503, "MCP configuration incomplete")
    return {"status": "ready", "file_sources_configured": bool(s.file_sources)}


@app.get("/.well-known/oauth-protected-resource")
@app.get("/.well-known/oauth-protected-resource/mcp")
async def metadata():
    s = get_mcp_settings()
    return {"resource": s.resource, "authorization_servers": [s.issuer], "scopes_supported": [READ, WRITE],
            "bearer_methods_supported": ["header"]}


def rpc_error(ident, code, message, status=200):
    return JSONResponse({"jsonrpc": "2.0", "id": ident, "error": {"code": code, "message": message}}, status_code=status)


@app.post("/mcp")
async def mcp(request: Request):
    global upload_busy
    s = get_mcp_settings()
    if request.url.hostname != urlsplit(s.resource).hostname:
        raise HTTPException(403, "Invalid MCP host")
    if request.headers.get("origin") and request.headers["origin"] not in s.allowed_origins:
        raise HTTPException(403, "Invalid MCP origin")
    try:
        async with asyncio.timeout(8):
            claims = await authenticate(request)
    except TimeoutError:
        raise HTTPException(503, "OAuth provider timed out") from None
    if "application/json" not in request.headers.get("content-type", "").lower():
        raise HTTPException(415, "JSON is required")
    accept = request.headers.get("accept", "")
    if "application/json" not in accept or "text/event-stream" not in accept:
        raise HTTPException(406, "Accept application/json and text/event-stream")
    version = request.headers.get("mcp-protocol-version", "2025-03-26")
    if version not in VERSIONS:
        raise HTTPException(400, "Unsupported MCP protocol version")
    body = bytearray()
    try:
        async with asyncio.timeout(15):
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > MAX_RPC_BYTES:
                    raise HTTPException(413, "MCP request too large")
    except TimeoutError:
        raise HTTPException(408, "MCP body read timed out") from None
    try:
        rpc = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return rpc_error(None, -32700, "Invalid JSON", 400)
    if (not isinstance(rpc, dict) or rpc.get("jsonrpc") != "2.0" or not isinstance(rpc.get("method"), str)
            or isinstance(rpc.get("id"), bool) or ("id" in rpc and not isinstance(rpc["id"], (str, int)))):
        return rpc_error(None, -32600, "Invalid JSON-RPC request", 400)
    method = rpc["method"]
    if "id" not in rpc:
        # Notifications never cause writes; they carry no response.
        return Response(status_code=202)
    ident = rpc["id"]
    params = rpc.get("params", {})
    if not isinstance(params, dict):
        return rpc_error(ident, -32602, "Invalid params")
    if method == "initialize":
        result = {"protocolVersion": params.get("protocolVersion") if params.get("protocolVersion") in VERSIONS else VERSIONS[0],
                  "capabilities": {"tools": {}}, "serverInfo": {"name": "autolog", "version": "1.0.0"},
                  "instructions": "Read get_writing_guidelines, categories and existing articles before saving. Only publish on user request. Reuse upload_image URLs. Never retry an uncertain write or version conflict blindly."}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        require_scope(claims, READ)
        result = {"tools": tools()}
    elif method == "tools/call":
        name = params.get("name")
        if not isinstance(name, str) or name not in CONTRACTS:
            return rpc_error(ident, -32602, "Unknown tool")
        model, scope, _ = CONTRACTS[name]
        require_scope(claims, scope)
        try:
            value = model.model_validate(params.get("arguments", {}))
        except ValidationError:
            return rpc_error(ident, -32602, "Invalid tool arguments")
        acquired = False
        try:
            if name == "upload_image":
                if upload_busy:
                    raise HTTPException(429, "Image upload busy; wait before retrying")
                upload_busy = True
                acquired = True
            async with asyncio.timeout(45):
                output = await call_tool(name, value, claims["sub"])
            structured = output if isinstance(output, dict) else {"items": output}
            result = {"content": [{"type": "text", "text": json.dumps(structured, ensure_ascii=False)}],
                      "structuredContent": structured, "isError": False}
        except HTTPException as error:
            result = {"content": [{"type": "text", "text": json.dumps({"error": {"status": error.status_code, "message": error.detail}})}], "isError": True}
        except Exception:
            result = {"content": [{"type": "text", "text": "Operation failed; check its outcome before retrying. No automatic retry performed."}], "isError": True}
        finally:
            if acquired:
                upload_busy = False
    else:
        return rpc_error(ident, -32601, "Method not found")
    return JSONResponse({"jsonrpc": "2.0", "id": ident, "result": result}, headers={"Cache-Control": "no-store"})
