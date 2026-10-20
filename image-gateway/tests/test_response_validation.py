"""Security boundaries in the gateway handler without thread/HTTP fixtures."""
from io import BytesIO
from unittest.mock import Mock

import pytest
from fastapi import HTTPException, Request

from gateway.main import MAX_BYTES, image


def get_image(reader, method="GET"):
    return image("2026/10/example.png", Request({"type": "http", "method": method}), reader)


def object_reader(data=b"image bytes", **metadata):
    body = BytesIO(data)
    result = {"Body": body, "ContentType": "image/png", "ContentLength": len(data), **metadata}
    reader = Mock()
    reader.get_object.return_value = result
    reader.head_object.return_value = result
    return reader, body


def test_get_preserves_bytes_and_closes_source():
    reader, body = object_reader()
    result = get_image(reader)
    assert result.status_code == 200 and result.body == b"image bytes" and body.closed
    assert result.headers["x-content-type-options"] == "nosniff"
    reader.get_object.assert_called_once_with(Bucket="autolog-public", Key="uploads/2026/10/example.png")
    reader.head_object.assert_not_called()


def test_head_never_reads_body():
    reader, body = object_reader()
    result = get_image(reader, "HEAD")
    assert result.status_code == 200 and result.body == b""
    assert result.headers["content-length"] == "11"
    reader.get_object.assert_not_called()
    body.close()


@pytest.mark.parametrize("metadata", [
    {"ContentType": "text/html"}, {"ContentType": "image/png\r\nX-Evil: yes"},
    {"ContentLength": MAX_BYTES + 1}, {"ContentLength": -1},
    {"ContentLength": 10}, {"ContentLength": 12},
])
def test_unsafe_or_inconsistent_metadata_rejected_before_response(metadata):
    reader, body = object_reader(**metadata)
    with pytest.raises(HTTPException) as error:
        get_image(reader)
    assert error.value.status_code == 502 and body.closed


def test_broken_source_closes_and_hides_error():
    reader, body = object_reader()
    broken = Mock()
    broken.read.side_effect = OSError("upstream credential must stay private")
    reader.get_object.return_value["Body"] = broken
    with pytest.raises(HTTPException) as error:
        get_image(reader)
    assert error.value.status_code == 502 and "credential" not in error.value.detail
    broken.close.assert_called_once()
    body.close()
