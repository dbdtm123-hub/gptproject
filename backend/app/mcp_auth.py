"""OAuth resource server. Authorization-code/PKCE issuance belongs to the configured IdP."""
import time

import httpx
from fastapi import HTTPException, Request

from app.mcp_config import get_mcp_settings

READ = "autolog:read"
WRITE = "autolog:write"


def challenge(error="invalid_token", scope=READ):
    settings = get_mcp_settings()
    origin = settings.resource.split("/", 3)[:3]
    metadata = "/".join(origin) + "/.well-known/oauth-protected-resource"
    return f'Bearer resource_metadata="{metadata}", scope="{scope}", error="{error}"'


async def introspect(token: str) -> dict:
    settings = get_mcp_settings()
    if not settings.introspection_client_id or not settings.introspection_client_secret.get_secret_value() or not settings.allowed_subjects:
        raise HTTPException(503, "MCP OAuth is not configured")
    try:
        async with httpx.AsyncClient(timeout=5, follow_redirects=False) as client:
            async with client.stream("POST", settings.introspection_url,
                                     data={"token": token, "token_type_hint": "access_token"},
                                     auth=(settings.introspection_client_id, settings.introspection_client_secret.get_secret_value())) as response:
                if response.status_code != 200:
                    raise HTTPException(503, "OAuth provider unavailable")
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > 16384:
                        raise HTTPException(503, "OAuth provider unavailable")
                import json
                return json.loads(data)
    except (httpx.HTTPError, ValueError):
        raise HTTPException(503, "OAuth provider unavailable") from None


async def authenticate(request: Request) -> dict:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer ") or not 1 <= len(header[7:]) <= 8192:
        raise HTTPException(401, "OAuth authentication required", headers={"WWW-Authenticate": challenge()})
    claims = await introspect(header[7:])
    settings = get_mcp_settings()
    valid = isinstance(claims, dict)
    audience = claims.get("aud", []) if valid else []
    if isinstance(audience, str):
        audience = [audience]
    expiry = claims.get("exp") if valid else None
    if (not valid or claims.get("active") is not True or claims.get("iss") != settings.issuer
            or not isinstance(audience, list) or settings.resource not in audience
            or not isinstance(expiry, (int, float)) or isinstance(expiry, bool) or not time.time() < expiry < float("inf")
            or claims.get("sub") not in settings.allowed_subjects or not isinstance(claims.get("scope"), str)):
        raise HTTPException(401, "Invalid OAuth access token", headers={"WWW-Authenticate": challenge()})
    return claims


def require_scope(claims: dict, scope: str):
    if scope not in claims["scope"].split():
        raise HTTPException(403, "OAuth scope required", headers={"WWW-Authenticate": challenge("insufficient_scope", scope)})
