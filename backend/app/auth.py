"""Public reading, password-backed admin sessions, and write-only client tokens."""
from collections import OrderedDict, deque
from threading import Lock
import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.config import get_settings

COOKIE_NAME = "autolog_admin"
SESSION_SECONDS = 8 * 60 * 60
bearer = HTTPBearer(auto_error=False, scheme_name="ApiToken")
router = APIRouter(prefix="/api/auth", tags=["authentication"])
login_failures: OrderedDict[str, deque[float]] = OrderedDict()
login_lock = Lock()


def session_key() -> bytes | None:
    settings = get_settings()
    password = getattr(settings, "app_password", None)
    if not password or not password.get_secret_value():
        return None
    return hashlib.sha256((password.get_secret_value() + "\0autolog-admin-session-v1").encode()).digest()


def encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def create_session() -> str:
    key = session_key()
    if key is None:
        raise HTTPException(503, "Admin login is not configured")
    payload = encode(json.dumps({"user": get_settings().app_username, "exp": int(time.time()) + SESSION_SECONDS,
                                "nonce": secrets.token_urlsafe(16)}, separators=(",", ":")).encode())
    return payload + "." + encode(hmac.new(key, payload.encode(), hashlib.sha256).digest())


def is_admin(request: Request) -> bool:
    token = request.cookies.get(COOKIE_NAME, "")
    key = session_key()
    if not token or len(token) > 2048 or key is None:
        return False
    try:
        payload, signature = token.split(".")
        expected = encode(hmac.new(key, payload.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            return False
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return data["user"] == get_settings().app_username and int(time.time()) < data["exp"] <= int(time.time()) + SESSION_SECONDS
    except (ValueError, KeyError, TypeError):
        return False


def require_admin(request: Request) -> None:
    if not is_admin(request):
        raise HTTPException(401, "Admin login required")


def require_api_token(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]) -> None:
    token = get_settings().api_token
    if token is None or not token.get_secret_value():
        raise HTTPException(503, "API authentication is not configured")
    expected = hashlib.sha256(token.get_secret_value().encode()).digest()
    supplied = hashlib.sha256((credentials.credentials if credentials else "").encode()).digest()
    if credentials is None or not hmac.compare_digest(expected, supplied):
        raise HTTPException(401, "Invalid or missing API token", headers={"WWW-Authenticate": "Bearer"})


def require_write_access(request: Request,
                         credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]) -> None:
    # Explicit external credentials must be valid, even when an admin cookie is also present.
    if request.headers.get("authorization"):
        require_api_token(credentials)
        return
    if not is_admin(request):
        raise HTTPException(401, "Admin session or API token required", headers={"WWW-Authenticate": "Bearer"})
    origin = request.headers.get("origin")
    if origin and origin.rstrip("/") not in get_settings().cors_origins:
        raise HTTPException(403, "Cross-origin writes are not allowed")


class Login(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    username: str = Field(min_length=1, max_length=200)
    password: SecretStr = Field(min_length=1, max_length=1024)


@router.post("/login")
def login(payload: Login, request: Request) -> dict[str, str | int]:
    settings = get_settings()
    key = session_key()
    if key is None:
        raise HTTPException(503, "Admin login is not configured")
    client = request.client.host if request.client else "unknown"
    with login_lock:
        now = time.monotonic()
        attempts = login_failures.setdefault(client, deque())
        while attempts and attempts[0] < now - 60:
            attempts.popleft()
        if len(attempts) >= 10:
            raise HTTPException(429, "Too many login attempts; retry in one minute", headers={"Retry-After": "60"})
        attempts.append(now)
        login_failures.move_to_end(client)
        if len(login_failures) > 2048:
            login_failures.popitem(last=False)
    digest = lambda value: hashlib.sha256(value.encode()).digest()
    valid_user = hmac.compare_digest(digest(payload.username), digest(settings.app_username))
    valid_password = hmac.compare_digest(digest(payload.password.get_secret_value()), digest(settings.app_password.get_secret_value()))
    if not (valid_user and valid_password):
        raise HTTPException(401, "Invalid username or password")
    with login_lock:
        login_failures.pop(client, None)
    return {"session": create_session(), "max_age": SESSION_SECONDS}


@router.get("/me", dependencies=[Depends(require_admin)])
def me() -> dict[str, str]:
    return {"username": get_settings().app_username}
