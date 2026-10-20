"""Image decoder security checks, independent of PostgreSQL and HTTP servers."""
from io import BytesIO

import pytest
from fastapi import HTTPException
from PIL import Image

from app import uploads


def encoded_image(format, **options):
    output = BytesIO()
    Image.new("RGB", (4, 4), "green").save(output, format=format, **options)
    return output.getvalue()


@pytest.mark.parametrize("format,mime,extension", [
    ("PNG", "image/png", "png"), ("JPEG", "image/jpeg", "jpg"),
    ("WEBP", "image/webp", "webp"), ("GIF", "image/gif", "gif"),
])
def test_decoder_accepts_supported_file_bytes(format, mime, extension):
    assert uploads.validate_image(encoded_image(format), mime) == (mime, extension)


@pytest.mark.parametrize("data,mime,status", [
    (b"<svg/>", "image/svg+xml", 415),
    (b"not an image", "image/png", 422),
    (encoded_image("PNG"), "image/jpeg", 415),
    (encoded_image("PNG")[:20], "image/png", 422),
])
def test_decoder_rejects_spoofed_and_invalid_files(data, mime, status):
    with pytest.raises(HTTPException) as error:
        uploads.validate_image(data, mime)
    assert error.value.status_code == status


def test_decoded_pixel_budget_includes_all_animation_frames(monkeypatch):
    output = BytesIO()
    Image.new("RGB", (4, 4), "red").save(
        output, format="GIF", save_all=True,
        append_images=[Image.new("RGB", (4, 4), "blue")],
    )
    monkeypatch.setattr(uploads, "MAX_DECODE_PIXELS", 31)
    with pytest.raises(HTTPException) as error:
        uploads.validate_image(output.getvalue(), "image/gif")
    assert error.value.status_code == 422
    monkeypatch.setattr(uploads, "MAX_DECODE_PIXELS", 32)
    assert uploads.validate_image(output.getvalue(), "image/gif") == ("image/gif", "gif")


def test_animation_frame_limit():
    output = BytesIO()
    frames = [Image.new("RGB", (1, 1), (i, 0, 0)) for i in range(101)]
    frames[0].save(output, format="GIF", save_all=True, append_images=frames[1:])
    with pytest.raises(HTTPException) as error:
        uploads.validate_image(output.getvalue(), "image/gif")
    assert error.value.status_code == 422


def test_pillow_decompression_bomb_warning_is_rejected(monkeypatch):
    data = encoded_image("PNG")
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 10)
    with pytest.raises(HTTPException) as error:
        uploads.validate_image(data, "image/png")
    assert error.value.status_code == 422
