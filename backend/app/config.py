from functools import lru_cache
import hashlib
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, SecretStr, StringConstraints, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ReportReader(BaseModel):
    """Operator-assigned ownership for existing stable IDs; never client-supplied."""
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    token: SecretStr = Field(min_length=32)
    report_ids: frozenset[Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+-[0-9]{4,}$", max_length=100)]]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file="../.env", extra="ignore", hide_input_in_errors=True)

    database_url: str = Field(min_length=1)
    cors_origins: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]
    app_username: str = "autolog"
    app_password: SecretStr | None = None
    api_token: SecretStr | None = Field(default=None, min_length=32)
    report_read_token: SecretStr | None = Field(default=None, min_length=32)
    report_write_token: SecretStr | None = Field(default=None, min_length=32)
    report_readers: dict[str, ReportReader] = Field(default_factory=dict, repr=False)
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
        assigned = set()
        digests = {hashlib.sha256(value.encode()).digest() for value in values}
        for subject, reader in self.report_readers.items():
            if not subject.strip() or len(subject) > 100:
                raise ValueError("Report reader subject must be nonempty and at most 100 characters")
            digest = hashlib.sha256(reader.token.get_secret_value().encode()).digest()
            if digest in digests or assigned.intersection(reader.report_ids):
                raise ValueError("Report reader credentials and report ownership must be unique")
            digests.add(digest)
            assigned.update(reader.report_ids)
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
