from io import BytesIO
from unittest.mock import Mock
import pytest
from fastapi.testclient import TestClient
from botocore.exceptions import ClientError, EndpointConnectionError
from gateway.main import app, MAX_BYTES, CACHE
from gateway import storage
from gateway.config import get_settings

PATH = "/uploads/2026/10/example.png"


@pytest.fixture
def reader():
    client = Mock()
    client.get_object.side_effect = lambda **kwargs: {
        "Body": BytesIO(b"image bytes"),
        "ContentType": "image/png",
        "ContentLength": 11,
        "ETag": '"object-etag"',
    }
    client.head_object.return_value = {
        "ContentType": "image/png",
        "ContentLength": 11,
        "ETag": '"object-etag"',
    }
    app.dependency_overrides[storage.get_reader] = lambda: client
    yield client
    app.dependency_overrides.clear()


@pytest.fixture
def client(reader):
    with TestClient(app) as client:
        yield client


def test_get_image(client, reader):
    response = client.get(PATH)
    assert response.status_code == 200 and response.content == b"image bytes"
    assert response.headers["content-type"] == "image/png"
    assert (
        response.headers["cache-control"] == CACHE
        and response.headers["etag"] == '"object-etag"'
    )
    assert response.headers["x-content-type-options"] == "nosniff"
    reader.get_object.assert_called_once_with(
        Bucket="autolog-public", Key="uploads/2026/10/example.png"
    )
    reader.head_object.assert_not_called()


def test_head_image(client, reader):
    response = client.head(PATH)
    assert (
        response.status_code == 200
        and response.content == b""
        and response.headers["content-length"] == "11"
    )
    reader.head_object.assert_called_once_with(
        Bucket="autolog-public", Key="uploads/2026/10/example.png"
    )
    reader.get_object.assert_not_called()


@pytest.mark.parametrize(
    "method", ["post", "put", "patch", "delete", "options", "trace"]
)
def test_unsupported_methods(client, reader, method):
    response = (
        getattr(client, method)(PATH)
        if method != "trace"
        else client.request("TRACE", PATH)
    )
    assert response.status_code == 405 and response.headers["allow"] == "GET, HEAD"
    reader.get_object.assert_not_called()
    reader.head_object.assert_not_called()


@pytest.mark.parametrize(
    "path",
    [
        "/secret.png",
        "/autolog-public/uploads/2026/10/example.png",
        "/uploads/../secret.png",
        "/uploads/2026/10/../secret.png",
        "/uploads/2026/10/%2e%2e%2fsecret.png",
        "/uploads/2026/10/%252e%252e%252fsecret.png",
        "/uploads/2026/10/a%5cb.png",
        "/uploads/2026/10/a.svg",
        "/uploads/2026/00/a.png",
        "/uploads/2026/10/a.png?list-type=2",
    ],
)
def test_invalid_path_never_reaches_s3(client, reader, path):
    assert 400 <= client.get(path).status_code < 500
    reader.get_object.assert_not_called()
    reader.head_object.assert_not_called()


@pytest.mark.parametrize("method", ["get", "head"])
def test_missing_object(client, reader, method):
    failure = ClientError(
        {"Error": {"Code": "NoSuchKey", "Message": "private"}}, "GetObject"
    )
    reader.get_object.side_effect = failure
    reader.head_object.side_effect = failure
    response = getattr(client, method)(PATH)
    assert (
        response.status_code == 404 and response.headers["cache-control"] == "no-store"
    )
    assert "private" not in response.text


@pytest.mark.parametrize(
    "error",
    [
        EndpointConnectionError(endpoint_url="http://internal-secret"),
        ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "private-signature"}},
            "GetObject",
        ),
    ],
)
def test_storage_unavailable(client, reader, error):
    reader.get_object.side_effect = error
    result = client.get(PATH)
    assert result.status_code == 502
    assert (
        "private-signature" not in result.text and "internal-secret" not in result.text
    )
    assert result.headers["cache-control"] == "no-store"


def test_incomplete_body_is_safe_502_before_response(client, reader):
    body = BytesIO(b"short")
    reader.get_object.side_effect = None
    reader.get_object.return_value = {
        "Body": body,
        "ContentType": "image/png",
        "ContentLength": 10,
    }
    assert client.get(PATH).status_code == 502 and body.closed


def test_broken_stream_returns_json_and_closes(client, reader):
    body = Mock()
    body.read.side_effect = OSError("raw endpoint/secret")
    reader.get_object.side_effect = None
    reader.get_object.return_value = {
        "Body": body,
        "ContentType": "image/png",
        "ContentLength": 10,
    }
    response = client.get(PATH)
    assert response.status_code == 502 and "raw endpoint" not in response.text
    body.close.assert_called_once()


@pytest.mark.parametrize(
    "mime,size",
    [("text/html", 10), ("image/svg+xml", 10), ("image/png", MAX_BYTES + 1)],
)
def test_unsafe_metadata(client, reader, mime, size):
    body = BytesIO(b"unsafe")
    reader.get_object.side_effect = None
    reader.get_object.return_value = {
        "Body": body,
        "ContentType": mime,
        "ContentLength": size,
    }
    assert client.get(PATH).status_code == 502 and body.closed


def test_only_dedicated_reader_credentials(monkeypatch):
    get_settings.cache_clear()
    storage.get_reader.cache_clear()
    monkeypatch.setenv("S3_ACCESS_KEY", "writer-never-used")
    monkeypatch.setenv("S3_SECRET_KEY", "writer-never-used")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "writer-never-used")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "writer-never-used")
    monkeypatch.setenv("GATEWAY_S3_ACCESS_KEY", "reader-test-id")
    monkeypatch.setenv("GATEWAY_S3_SECRET_KEY", "reader-test-secret")
    mock = Mock()
    monkeypatch.setattr(storage.boto3, "client", mock)
    try:
        storage.get_reader()
        kwargs = mock.call_args.kwargs
        assert (
            kwargs["aws_access_key_id"] == "reader-test-id"
            and kwargs["aws_secret_access_key"] == "reader-test-secret"
        )
        assert kwargs["endpoint_url"] == "http://garage.lifelog.svc.cluster.local:3900"
    finally:
        get_settings.cache_clear()
        storage.get_reader.cache_clear()


def test_no_fallback_to_write_key(monkeypatch):
    get_settings.cache_clear()
    storage.get_reader.cache_clear()
    monkeypatch.delenv("GATEWAY_S3_ACCESS_KEY", raising=False)
    monkeypatch.delenv("GATEWAY_S3_SECRET_KEY", raising=False)
    monkeypatch.setenv("S3_ACCESS_KEY", "writer-never-used")
    monkeypatch.setenv("S3_SECRET_KEY", "writer-never-used")
    mock = Mock()
    monkeypatch.setattr(storage.boto3, "client", mock)
    try:
        with TestClient(app) as client:
            assert client.get("/health/ready").status_code == 503
            assert client.get(PATH).status_code == 503
        mock.assert_not_called()
    finally:
        get_settings.cache_clear()
        storage.get_reader.cache_clear()
