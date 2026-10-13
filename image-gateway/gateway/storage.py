"""Read-only operation adapter. No write, policy, presign or website API calls."""

from functools import lru_cache
import boto3
from botocore.config import Config
from fastapi import HTTPException
from gateway.config import get_settings


def reader_configured() -> bool:
    s = get_settings()
    return bool(
        s.s3_access_key
        and s.s3_secret_key
        and s.s3_access_key.get_secret_value()
        and s.s3_secret_key.get_secret_value()
    )


@lru_cache
def get_reader():
    if not reader_configured():
        raise HTTPException(503, "Image reader is not configured")
    s = get_settings()
    return boto3.client(
        "s3",
        endpoint_url=s.s3_endpoint,
        region_name=s.s3_region,
        aws_access_key_id=s.s3_access_key.get_secret_value(),
        aws_secret_access_key=s.s3_secret_key.get_secret_value(),
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            connect_timeout=3,
            read_timeout=10,
            retries={"total_max_attempts": 2},
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        ),
    )
