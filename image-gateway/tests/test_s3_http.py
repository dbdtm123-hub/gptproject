"""Exercise real boto3 signing/path mapping against a local S3 HTTP stub."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from fastapi.testclient import TestClient

from gateway.config import get_settings
from gateway.main import app
from gateway.storage import get_reader


def test_real_signed_get_and_head_use_reader_only(monkeypatch):
    image = b"image bytes"
    observed = []

    class S3Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.reply(head=False)

        def do_HEAD(self):
            self.reply(head=True)

        def reply(self, head):
            observed.append(
                (self.command, self.path, self.headers.get("Authorization"))
            )
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(image)))
            self.send_header("ETag", '"http-object"')
            self.end_headers()
            if not head:
                self.wfile.write(image)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), S3Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    get_settings.cache_clear()
    get_reader.cache_clear()
    monkeypatch.setenv("GATEWAY_S3_ENDPOINT", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setenv("GATEWAY_S3_ACCESS_KEY", "http-test-reader")
    monkeypatch.setenv("GATEWAY_S3_SECRET_KEY", "http-test-secret")
    monkeypatch.setenv("S3_ACCESS_KEY", "writer-must-not-be-used")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "writer-must-not-be-used")
    # Isolate the localhost-only stub from cloud SDK profile selectors.
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.delenv("AWS_DEFAULT_PROFILE", raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    try:
        with TestClient(app) as client:
            path = "/uploads/2026/10/example.png"
            response = client.get(
                path,
                headers={
                    "Authorization": "Bearer ignored-client-token",
                    "Cookie": "ignored=1",
                },
            )
            assert response.status_code == 200 and response.content == image
            assert response.headers["etag"] == '"http-object"'
            response = client.head(path)
            assert response.status_code == 200 and response.content == b""
        assert [method for method, _, _ in observed] == ["GET", "HEAD"]
        for _, path, authorization in observed:
            assert path == "/autolog-public/uploads/2026/10/example.png"
            assert authorization.startswith("AWS4-HMAC-SHA256 ")
            assert "Credential=http-test-reader/" in authorization
            assert "writer-must-not-be-used" not in authorization
            assert "ignored-client-token" not in authorization
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        get_reader.cache_clear()
        get_settings.cache_clear()
