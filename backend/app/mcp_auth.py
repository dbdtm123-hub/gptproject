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
    token = header[7:]
    claims = await introspect(token)
    settings = get_mcp_settings()
    valid = isinstance(claims, dict)
    # RFC 7662 token_type is optional. The verified Authelia v4 opaque access
    # token format uses authelia_at_ vs authelia_rt_ for refresh tokens. Do not
    # accept the latter: Authelia's introspection returns active=True for both.
    standard_access_type = (valid and isinstance(claims.get("token_type"), str)
                            and claims["token_type"].lower() == "bearer"
                            and claims.get("token_use", "access_token") == "access_token")
    authelia_access_type = (valid and settings.access_token_format == "authelia_opaque_v4"
                            and "token_type" not in claims and "token_use" not in claims
                            and token.startswith("authelia_at_"))
    token_kind_valid = standard_access_type or authelia_access_type
    audience = claims.get("aud", []) if valid else []
    if isinstance(audience, str):
        audience = [audience]
    expiry = claims.get("exp") if valid else None
    if (not valid or claims.get("active") is not True or claims.get("iss") != settings.issuer
            or not isinstance(audience, list) or settings.resource not in audience
            or not isinstance(expiry, (int, float)) or isinstance(expiry, bool) or not time.time() < expiry < float("inf")
            or not isinstance(claims.get("sub"), str) or claims.get("sub") not in settings.allowed_subjects
            or not isinstance(claims.get("scope"), str)
            or not token_kind_valid):
        raise HTTPException(401, "Invalid OAuth access token", headers={"WWW-Authenticate": challenge()})
    if settings.subject_scopes:
        permitted = settings.subject_scopes.get(claims["sub"], [])
        claims = {**claims, "scope": " ".join(scope for scope in claims["scope"].split() if scope in permitted)}
    return claims


def require_scope(claims: dict, scope: str):
    if scope not in claims["scope"].split():
        raise HTTPException(403, "OAuth scope required", headers={"WWW-Authenticate": challenge("insufficient_scope", scope)})
