"""Small, injectable S3 adapter. No network calls or bucket mutation at startup."""
import ipaddress
from urllib.parse import urlsplit

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import HTTPException

from app.config import get_settings


def public_base_url() -> str:
    value = get_settings().s3_public_base_url or ""
    try:
        url = urlsplit(value)
    except ValueError:
        raise HTTPException(503, "Public image URL is not configured correctly") from None
    host = url.hostname or ""
    try:
        private_ip = not ipaddress.ip_address(host).is_global
    except ValueError:
        private_ip = False
    if (url.scheme != "https" or "." not in host or url.username or url.password or url.query or url.fragment
            or private_ip or host.endswith((".svc", ".cluster.local", ".localhost", ".local"))):
        raise HTTPException(503, "Public image URL is not configured correctly")
    return value.rstrip("/")


def s3_client():
    settings = get_settings()
    if not settings.s3_access_key or not settings.s3_secret_key or not settings.s3_access_key.get_secret_value() or not settings.s3_secret_key.get_secret_value():
        raise HTTPException(503, "Image storage is not configured")
    return boto3.client("s3", endpoint_url=settings.s3_endpoint,
                        aws_access_key_id=settings.s3_access_key.get_secret_value(),
                        aws_secret_access_key=settings.s3_secret_key.get_secret_value(),
                        region_name=settings.s3_region,
                        config=Config(signature_version="s3v4", s3={"addressing_style": "path"},
                                      connect_timeout=3, read_timeout=10, retries={"total_max_attempts": 2},
                                      request_checksum_calculation="when_required", response_checksum_validation="when_required"))


class ImageStorage:
    def put(self, key: str, data: bytes, mime: str) -> None:
        try:
            s3_client().put_object(Bucket=get_settings().s3_bucket, Key=key, Body=data,
                                   ContentType=mime, CacheControl="public, max-age=31536000, immutable")
        except (BotoCoreError, ClientError):
            # Never return SDK errors, signed requests, endpoint URLs or credentials.
            raise HTTPException(502, "Image storage is unavailable; please retry") from None


def get_image_storage() -> ImageStorage:
    return ImageStorage()
