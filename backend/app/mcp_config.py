"""Independent MCP process settings: no DB or S3 credentials required."""
from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class MCPSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AUTOLOG_MCP_", env_file=None, hide_input_in_errors=True)
    resource: str = "https://mcp.example.com/mcp"
    issuer: str = "https://identity.example.com"
    introspection_url: str = "https://identity.example.com/introspect"
    introspection_client_id: str = ""
    introspection_client_secret: SecretStr = SecretStr("")
    # Strict default. Authelia v4's opaque tokens omit optional RFC 7662 token_type;
    # opt-in mode additionally verifies the token is an Authelia access token.
    access_token_format: Literal["rfc7662", "authelia_opaque_v4"] = "rfc7662"
    allowed_subjects: list[str] = []
    subject_scopes: dict[str, list[str]] = {}
    allowed_origins: list[str] = []
    backend_url: str = "http://lifelog-backend:8000"
    backend_token: SecretStr = SecretStr("")
    image_public_base: str = "https://img.example.com"
    blog_public_base: str = "https://autolog.example.com"
    private_reads: bool = False
    # Exact source hostname -> allowed path prefixes. Empty means uploads fail closed.
    file_sources: dict[str, list[str]] = {}

    @field_validator("resource", "issuer", "introspection_url", "image_public_base", "blog_public_base")
    @classmethod
    def https_only(cls, value):
        url = urlsplit(value)
        if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError("A fixed HTTPS URL is required")
        return value.rstrip("/")

    @field_validator("backend_url")
    @classmethod
    def backend_origin(cls, value):
        url = urlsplit(value)
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or url.path not in {"", "/"} or url.query or url.fragment:
            raise ValueError("A fixed backend origin is required")
        return value.rstrip("/")


@lru_cache
def get_mcp_settings():
    return MCPSettings()
