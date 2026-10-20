"""Exercise pre-parser authorization and streamed limits without a DB/server."""
import asyncio
import os

import pytest
from starlette.exceptions import HTTPException

from app.uploads import MAX_UPLOAD_BODY, UploadGuard


def run_guard(headers, chunks, consume=True):
    received, sent = [], []

    async def downstream(scope, receive, send):
        if consume:
            while True:
                message = await receive()
                if not message.get("more_body"):
                    break
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    pending = iter(chunks)

    async def receive():
        received.append(True)
        return next(pending)

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": "POST", "path": "/api/uploads",
             "headers": [(k.encode(), v.encode()) for k, v in headers.items()]}
    asyncio.run(UploadGuard(downstream)(scope, receive, send))
    return received, sent


def token_headers(**extra):
    return {"authorization": "Bearer " + os.environ["API_TOKEN"], **extra}


@pytest.mark.parametrize("headers", [{}, {"authorization": "Bearer invalid"}])
def test_auth_rejects_before_reading_body(headers):
    received, sent = run_guard(headers, [])
    assert received == []
    assert sent[0]["status"] == 401


def test_declared_oversize_rejected_without_reading():
    received, sent = run_guard(token_headers(**{"content-length": str(MAX_UPLOAD_BODY + 1)}), [])
    assert received == []
    assert sent[0]["status"] == 413


def test_invalid_length_is_safe_400():
    received, sent = run_guard(token_headers(**{"content-length": "invalid"}), [])
    assert received == []
    assert sent[0]["status"] == 400


def test_stream_limit_applies_even_with_understated_length():
    with pytest.raises(HTTPException) as error:
        run_guard(token_headers(**{"content-length": "1"}), [
            {"type": "http.request", "body": b"x" * MAX_UPLOAD_BODY, "more_body": True},
            {"type": "http.request", "body": b"x", "more_body": False},
        ])
    assert error.value.status_code == 413


def test_exact_total_body_budget_can_reach_parser():
    received, sent = run_guard(token_headers(), [
        {"type": "http.request", "body": b"x" * MAX_UPLOAD_BODY, "more_body": False},
    ])
    assert len(received) == 1
    assert sent[0]["status"] == 204
