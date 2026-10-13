from functools import lru_cache
from typing import Literal
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Deliberately do not read the backend .env or its S3/AWS credentials.
    model_config = SettingsConfigDict(
        env_prefix="GATEWAY_", env_file=None, extra="ignore", hide_input_in_errors=True
    )
    s3_endpoint: str = "http://garage.lifelog.svc.cluster.local:3900"
    s3_bucket: Literal["autolog-public"] = "autolog-public"
    s3_region: str = "garage"
    s3_access_key: SecretStr | None = None
    s3_secret_key: SecretStr | None = None


@lru_cache
def get_settings():
    return Settings()
