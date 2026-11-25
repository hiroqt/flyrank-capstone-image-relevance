"""Application settings, read from environment variables (and .env) only.

Secrets are SecretStr so they never print in reprs, logs or tracebacks (shared requirement #6).
"""
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database
    database_url: str = "postgresql+psycopg://app:app@localhost:5433/app"

    # Tenancy
    default_tenant: str = "demo"

    # AI provider
    ai_provider: Literal["gemini", "ollama", "fake"] = "gemini"
    gemini_api_key: SecretStr | None = None
    gemini_vision_model: str = "gemini-3.5-flash-lite"
    gemini_embed_model: str = "gemini-embedding-001"
    gemini_timeout_s: float = 60.0
    embed_dimensions: int = 768
    ollama_url: str = "http://ollama:11434"
    ollama_vision_model: str = "moondream"
    ollama_embed_model: str = "all-minilm"
    ollama_timeout_s: float = 180.0
    pricing_file: str = "config/pricing.yaml"

    # Cost + rate control
    ai_budget_usd: float = 1.00
    vision_rpm: int = 8
    embed_rpm: int = 60

    # Vision pipeline
    vision_min_confidence: float = 0.70
    vision_max_repairs: int = 2
    vision_max_side_px: int = 1024
    images_dir: str = "data/images"

    # Background jobs
    job_max_attempts: int = 3
    job_backoff_base_s: float = 2.0
    job_backoff_max_s: float = 60.0
    worker_poll_interval_s: float = 1.0
    worker_stale_after_s: int = 300

    # Ops
    alert_webhook_url: str | None = None
    log_level: str = "INFO"
    max_body_bytes: int = 1_000_000

    def resolve_path(self, path: str) -> Path:
        """Paths in settings and in the DB are relative to the project root unless absolute."""
        candidate = Path(path)
        return candidate if candidate.is_absolute() else PROJECT_ROOT / candidate

    def secret_values(self) -> list[str]:
        """Plain values of every secret, used by the log-redaction filter."""
        secrets = [self.gemini_api_key]
        return [s.get_secret_value() for s in secrets if s and s.get_secret_value()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
