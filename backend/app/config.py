from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file="../.env", extra="ignore", hide_input_in_errors=True)

    database_url: str = Field(min_length=1)
    cors_origins: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]
    app_username: str = "autolog"
    app_password: SecretStr | None = None
    api_token: SecretStr | None = Field(default=None, min_length=32)
    action_server_url: str = "https://autolog.example.com"
    enable_openai_classification: bool = False
    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-4o-mini"


@lru_cache
def get_settings() -> Settings:
    return Settings()
