"""Bounded public HTTPS file reader, with exact allowlist and IP-pinned TLS.

Only configured platform file hosts are reachable. file_id is a hint, not proof
of ownership; the authorized single-account caller supplies a capability URL,
whose signature/access is enforced by its source. No AutoLog credential leaves here.
"""
import http.client
import ipaddress
import socket
import ssl
import time
from urllib.parse import unquote, urlsplit

from fastapi import HTTPException

MAX_BYTES = 10 * 1024 * 1024


def source_target(value: str, sources: dict) -> tuple[str, str]:
    try:
        url = urlsplit(value)
        host = (url.hostname or "").lower()
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise HTTPException(422, "IP file sources are forbidden")
    try:
        url = urlsplit(value)
        host = (url.hostname or "").lower()
        path = unquote(url.path)
        if (len(value) > 8192 or url.scheme != "https" or url.port not in {None, 443}
                or url.username or url.password or url.fragment or not host or "\\" in value
                or any(ord(c) < 33 or ord(c) == 127 for c in value)
                or "%" in path or any(part in {".", ".."} for part in path.split("/"))):
            raise ValueError()
        prefixes = sources.get(host, [])
        if not prefixes or not any(prefix.startswith("/") and prefix.endswith("/") and path.startswith(prefix) for prefix in prefixes):
            raise HTTPException(403, "File source is not allowed")
        return host, url.path + ("?" + url.query if url.query else "")
    except (ValueError, UnboundLocalError):
        raise HTTPException(422, "Invalid file source") from None


def resolve_public(host: str) -> str:
    try:
        addresses = {entry[4][0] for entry in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
        if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
            raise HTTPException(403, "File source address is forbidden")
        return sorted(addresses)[0]
    except (OSError, ValueError):
        raise HTTPException(502, "File source unavailable") from None


class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, address):
        super().__init__(host, 443, timeout=3, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        # Connect to the validated IP; preserve the original hostname for TLS/SNI.
        raw = socket.create_connection((self.address, 443), timeout=self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


def download_image(url: str, sources: dict) -> bytes:
    if not sources:
        raise HTTPException(503, "Verified platform file sources are not configured")
    host, target = source_target(url, sources)
    address = resolve_public(host)
    deadline = time.monotonic() + 15
    connection = PinnedHTTPS(host, address)
    try:
        connection.request("GET", target, headers={"Accept": "image/*", "Accept-Encoding": "identity"})
        response = connection.getresponse()
        if response.status != 200:  # Includes all redirects: never follow them.
            raise HTTPException(502, "Selected file unavailable; select it again")
        if response.getheader("Content-Encoding", "identity").lower() not in {"identity", ""}:
            raise HTTPException(415, "Compressed file responses are unsupported")
        length = response.getheader("Content-Length")
        if length and (not length.isdigit() or int(length) > MAX_BYTES):
            raise HTTPException(413, "Image must be 10 MiB or smaller")
        data = bytearray()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise HTTPException(504, "Selected file read timed out")
            if connection.sock:
                connection.sock.settimeout(min(3, remaining))
            chunk = response.read(min(65536, MAX_BYTES + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > MAX_BYTES:
                raise HTTPException(413, "Image must be 10 MiB or smaller")
        if not data:
            raise HTTPException(422, "Image is empty")
        return bytes(data)
    except (OSError, http.client.HTTPException):
        raise HTTPException(502, "Selected file read failed") from None
    finally:
        connection.close()
