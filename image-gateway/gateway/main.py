from io import BytesIO
import re
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from botocore.exceptions import BotoCoreError, ClientError
from pydantic import ValidationError

from gateway.config import get_settings
from gateway.storage import get_reader, reader_configured

app = FastAPI(
    title="AutoLog image gateway", docs_url=None, redoc_url=None, openapi_url=None
)
MAX_BYTES = 10 * 1024 * 1024
CACHE = "public, max-age=31536000, immutable"
# Supports the uploader's UUID names and ordinary image names, e.g. example.png.
KEY = re.compile(
    r"uploads/\d{4}/(?:0[1-9]|1[0-2])/[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.(?:jpg|jpeg|png|webp|gif)\Z"
)
MIMES = {"image/jpeg", "image/png", "image/webp", "image/gif"}


def error(detail: str, status: int, method: str, headers=None):
    result = JSONResponse(
        {"detail": detail},
        status_code=status,
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            **(headers or {}),
        },
    )
    if method == "HEAD":
        return Response(status_code=status, headers=dict(result.headers))
    return result


@app.middleware("http")
async def restrict(request: Request, call_next):
    if request.method not in {"GET", "HEAD"}:
        return error("Method not allowed", 405, request.method, {"Allow": "GET, HEAD"})
    path = request.url.path
    if path not in {"/health/live", "/health/ready"}:
        if not path.startswith("/uploads/"):
            return error("Not found", 404, request.method)
        raw = request.scope.get("raw_path", b"").decode("ascii", errors="replace")
        if (
            request.url.query
            or "%" in raw
            or "\\" in path
            or ".." in path
            or not KEY.fullmatch(path[1:])
        ):
            return error("Invalid image path", 400, request.method)
    try:
        result = await call_next(request)
        result.headers["X-Content-Type-Options"] = "nosniff"
        if result.status_code >= 400:
            result.headers["Cache-Control"] = "no-store"
        return result
    except Exception:
        # A public gateway must not return SDK traces, credential values or signed request metadata.
        return error("Image service is unavailable", 502, request.method)


@app.get("/health/live")
def live():
    return {"status": "ok"}


@app.get("/health/ready")
def ready():
    try:
        if reader_configured():
            return {"status": "ready"}
    except ValidationError:
        pass
    raise HTTPException(503, "Image reader is not configured")


def object_headers(result):
    mime = result.get("ContentType", "")
    if (
        not isinstance(mime, str)
        or "\r" in mime
        or "\n" in mime
        or mime.split(";", 1)[0].lower() not in MIMES
    ):
        raise HTTPException(502, "Image metadata is invalid")
    size = result.get("ContentLength")
    if not isinstance(size, int) or not 0 <= size <= MAX_BYTES:
        raise HTTPException(502, "Image size is invalid")
    headers = {
        "Content-Type": mime,
        "Content-Length": str(size),
        "Cache-Control": CACHE,
        "X-Content-Type-Options": "nosniff",
    }
    etag = result.get("ETag")
    if (
        isinstance(etag, str)
        and len(etag) <= 256
        and not any(c in etag for c in ["\r", "\n"])
    ):
        headers["ETag"] = etag
    return headers, size


@app.api_route("/uploads/{image_path:path}", methods=["GET", "HEAD"])
def image(image_path: str, request: Request, reader=Depends(get_reader)):
    key = "uploads/" + image_path
    # Defense in depth if called without the middleware by a test or future router.
    if ".." in key or not KEY.fullmatch(key):
        raise HTTPException(400, "Invalid image path")
    body = None
    try:
        params = {"Bucket": get_settings().s3_bucket, "Key": key}
        if request.method == "HEAD":
            result = reader.head_object(**params)
            headers, _ = object_headers(result)
            return Response(status_code=200, headers=headers)
        result = reader.get_object(**params)
        body = result["Body"]
        headers, expected = object_headers(result)
        # Bound memory and complete the S3 read BEFORE sending HTTP headers so a broken
        # upstream body can still produce a safe 502 rather than a truncated 200.
        with BytesIO() as output:
            while chunk := body.read(64 * 1024):
                output.write(chunk)
                if output.tell() > MAX_BYTES or output.tell() > expected:
                    raise HTTPException(502, "Image size is invalid")
            if output.tell() != expected:
                raise HTTPException(502, "Image read was incomplete")
            return Response(content=output.getvalue(), status_code=200, headers=headers)
    except ClientError as exc:
        code = str(exc.response.get("Error", {}).get("Code", ""))
        if code in {"NoSuchKey", "NotFound", "404"}:
            raise HTTPException(404, "Image not found") from None
        raise HTTPException(502, "Image storage is unavailable") from None
    except (BotoCoreError, OSError, KeyError, TypeError, ValueError):
        raise HTTPException(502, "Image storage is unavailable") from None
    finally:
        if body is not None:
            try:
                body.close()
            except Exception:
                pass
