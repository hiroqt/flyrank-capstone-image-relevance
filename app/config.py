"""Application settings, read from environment variables (and .env) only.

Secrets are SecretStr so they never print in reprs, logs or tracebacks (shared requirement #6).
"""
from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database
    database_url: str = "postgresql+psycopg://app:app@localhost:5433/app"

    # Tenancy
    default_tenant: str = "demo"

    # AI provider
    ai_provider: Literal["gemini", "ollama", "fake"] = "gemini"
    gemini_api_key: SecretStr | None = None
    gemini_vision_model: str = "gemini-flash-model-id"
    gemini_embed_model: str = "gemini-embedding-model-id"
    ollama_url: str = "http://ollama:11434"
    ollama_vision_model: str = "moondream"
    ollama_embed_model: str = "all-minilm"

    # Cost + rate control
    ai_budget_usd: float = 1.00
    vision_rpm: int = 8
    embed_rpm: int = 60

    # Ops
    alert_webhook_url: str | None = None
    log_level: str = "INFO"
    max_body_bytes: int = 1_000_000

    def secret_values(self) -> list[str]:
        """Plain values of every secret, used by the log-redaction filter."""
        secrets = [self.gemini_api_key]
        return [s.get_secret_value() for s in secrets if s and s.get_secret_value()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
