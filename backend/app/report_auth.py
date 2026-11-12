"""Reports reuse the site's owner session/token; optional credentials are report-only."""
import hashlib
import hmac
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials

from app.auth import bearer, is_admin
from app.config import get_settings


def access(request: Request, credentials: HTTPAuthorizationCredentials | None, write: bool) -> str:
    settings = get_settings()
    if request.headers.get("authorization"):
        supplied = hashlib.sha256((credentials.credentials if credentials else "").encode()).digest()
        candidates = [(settings.api_token, "owner-api-token"), (settings.report_write_token, "report-write-token")]
        if not write:
            candidates.append((settings.report_read_token, "report-read-token"))
        for secret, actor in candidates:
            if secret and secret.get_secret_value() and credentials and hmac.compare_digest(
                supplied, hashlib.sha256(secret.get_secret_value().encode()).digest()
            ):
                return actor
        raise HTTPException(401, "Invalid or insufficient report credentials", headers={"WWW-Authenticate": "Bearer"})
    if not is_admin(request):
        raise HTTPException(401, "Report authentication required", headers={"WWW-Authenticate": "Bearer"})
    if write:
        origin = request.headers.get("origin")
        if origin and origin.rstrip("/") not in settings.cors_origins:
            raise HTTPException(403, "Cross-origin writes are not allowed")
    return "owner-admin-session"


def require_report_read(request: Request, credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]) -> str:
    return access(request, credentials, False)


def require_report_write(request: Request, credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]) -> str:
    return access(request, credentials, True)


class ReportPrivacyMiddleware:
    # Pure ASGI: do not wrap or consume the existing upload request body stream.
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if scope["type"] != "http" or not (path == "/api/reports" or path.startswith("/api/reports/")):
            return await self.app(scope, receive, send)
        from starlette.datastructures import MutableHeaders
        async def private_send(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["Cache-Control"] = "private, no-store"
                headers["Vary"] = "Authorization, Cookie"
            await send(message)
        await self.app(scope, receive, private_send)
