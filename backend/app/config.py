from functools import lru_cache

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file="../.env", extra="ignore", hide_input_in_errors=True)

    database_url: str = Field(min_length=1)
    cors_origins: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]
    app_username: str = "autolog"
    app_password: SecretStr | None = None
    api_token: SecretStr | None = Field(default=None, min_length=32)
    report_read_token: SecretStr | None = Field(default=None, min_length=32)
    report_write_token: SecretStr | None = Field(default=None, min_length=32)
    action_server_url: str = "https://autolog.example.com"
    enable_openai_classification: bool = False
    mcp_private_reads_enabled: bool = False
    openai_api_key: SecretStr | None = None
    s3_endpoint: str = "http://garage.lifelog.svc.cluster.local:3900"
    s3_access_key: SecretStr | None = None
    s3_secret_key: SecretStr | None = None
    s3_bucket: str = "autolog-public"
    s3_public_base_url: str | None = None
    s3_region: str = "garage"
    openai_model: str = "gpt-4o-mini"


    @model_validator(mode="after")
    def distinct_report_credentials(self):
        values = [secret.get_secret_value() for secret in [self.api_token, self.report_read_token, self.report_write_token] if secret]
        if len(values) != len(set(values)):
            raise ValueError("Owner and report-only credentials must be distinct")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
