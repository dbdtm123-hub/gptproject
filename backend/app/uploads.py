"""Authenticated image uploads with no Article/DB schema changes."""
from datetime import datetime, timezone
from io import BytesIO
from uuid import uuid4
import warnings

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.auth import bearer, require_write_access
from app.storage import ImageStorage, get_image_storage, public_base_url

MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_UPLOAD_BODY = MAX_IMAGE_BYTES + 64 * 1024  # Multipart headers/boundary, not a larger file allowance.
MAX_DECODE_PIXELS = 40_000_000
FORMATS = {"JPEG": ("image/jpeg", "jpg"), "PNG": ("image/png", "png"),
           "WEBP": ("image/webp", "webp"), "GIF": ("image/gif", "gif")}
router = APIRouter(prefix="/api", tags=["uploads"])


class UploadGuard:
    """Authenticate and bound the body before multipart parsing/spooling, including chunked requests."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] != "/api/uploads" or scope["method"] != "POST":
            return await self.app(scope, receive, send)
        request = Request(scope)
        try:
            require_write_access(request, await bearer(request))
            length = request.headers.get("content-length")
            if length and int(length) > MAX_UPLOAD_BODY:
                raise HTTPException(413, "Image must be 10 MiB or smaller")
        except ValueError:
            return await JSONResponse({"detail": "Invalid Content-Length"}, status_code=400)(scope, receive, send)
        except HTTPException as error:
            return await JSONResponse({"detail": error.detail}, status_code=error.status_code,
                                      headers=error.headers)(scope, receive, send)
        size = 0

        async def bounded_receive():
            nonlocal size
            message = await receive()
            if message["type"] == "http.request":
                size += len(message.get("body", b""))
                if size > MAX_UPLOAD_BODY:
                    raise StarletteHTTPException(413, "Upload request is too large")
            return message

        await self.app(scope, bounded_receive, send)


def validate_image(data: bytes, declared_mime: str) -> tuple[str, str]:
    if declared_mime not in {item[0] for item in FORMATS.values()}:
        raise HTTPException(415, "Only JPEG, PNG, WebP and GIF images are supported")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as image:
                detected = FORMATS.get(image.format)
                if detected is None or detected[0] != declared_mime:
                    raise HTTPException(415, "Image content does not match its MIME type")
                image.verify()
            with Image.open(BytesIO(data)) as image:
                pixels = 0
                frames = getattr(image, "n_frames", 1)
                if frames > 100:
                    raise HTTPException(422, "Image has too many animation frames")
                for index in range(frames):
                    image.seek(index)
                    pixels += image.width * image.height
                    if pixels > MAX_DECODE_PIXELS:
                        raise HTTPException(422, "Decoded image is too large")
                    image.load()  # Reject truncated or malformed pixel data, not just bad magic bytes.
        return detected
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError,
            Image.DecompressionBombWarning):
        raise HTTPException(422, "Invalid or unsafe image data") from None


class UploadResult(BaseModel):
    url: str
    key: str


@router.post("/uploads", response_model=UploadResult, status_code=201,
             dependencies=[Depends(require_write_access)], operation_id="uploadImage")
async def upload_image(file: UploadFile = File(...), storage: ImageStorage = Depends(get_image_storage)) -> UploadResult:
    base = public_base_url()
    data = bytearray()
    try:
        while chunk := await file.read(64 * 1024):
            data.extend(chunk)
            if len(data) > MAX_IMAGE_BYTES:
                raise HTTPException(413, "Image must be 10 MiB or smaller")
    finally:
        await file.close()
    if not data:
        raise HTTPException(422, "Image is empty")
    mime, extension = await run_in_threadpool(validate_image, bytes(data), file.content_type or "")
    key = datetime.now(timezone.utc).strftime("uploads/%Y/%m/") + uuid4().hex + "." + extension
    await run_in_threadpool(storage.put, key, bytes(data), mime)
    return UploadResult(url=base + "/" + key, key=key)
