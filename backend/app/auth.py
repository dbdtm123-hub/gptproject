"""Bearer authentication for every data API; never expose server tokens."""
import hashlib
import hmac
from typing import Annotated

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import get_settings

bearer = HTTPBearer(auto_error=False, scheme_name="ApiToken")


def require_api_token(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> None:
    token = get_settings().api_token
    if token is None or not token.get_secret_value():
        raise HTTPException(503, "API authentication is not configured")
    expected = hashlib.sha256(token.get_secret_value().encode()).digest()
    supplied = hashlib.sha256((credentials.credentials if credentials else "").encode()).digest()
    if credentials is None or not hmac.compare_digest(expected, supplied):
        raise HTTPException(401, "Invalid or missing API token", headers={"WWW-Authenticate": "Bearer"})
