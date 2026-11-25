"""Builds the configured providers (AI_PROVIDER = gemini | ollama | fake), already cost-tracked."""
from collections.abc import Callable

import httpx
from sqlalchemy.orm import Session

from app.config import Settings
from app.jobs.retry import RateLimiter
from app.providers.base import ProviderNotConfigured
from app.providers.pricing import PriceBook
from app.providers.tracked import CostLedger, TrackedEmbedder, TrackedVision


def build_ledger(settings: Settings, session_factory: Callable[[], Session]) -> CostLedger:
    prices = PriceBook.from_file(settings.resolve_path(settings.pricing_file))
    return CostLedger(session_factory, prices, settings.ai_budget_usd)


def build_vision(settings: Settings, ledger: CostLedger) -> TrackedVision:
    limiter = RateLimiter(settings.vision_rpm)
    if settings.ai_provider == "gemini":
        from app.providers.gemini import GeminiVision, make_client

        key = settings.gemini_api_key.get_secret_value() if settings.gemini_api_key else ""
        if not key:
            raise ProviderNotConfigured("GEMINI_API_KEY is not set. Copy .env.example to .env and add your key.")
        inner = GeminiVision(make_client(key, settings.gemini_timeout_s), settings.gemini_vision_model)
    elif settings.ai_provider == "ollama":
        from app.providers.ollama import OllamaVision

        client = httpx.Client(base_url=settings.ollama_url, timeout=settings.ollama_timeout_s)
        inner = OllamaVision(client, settings.ollama_vision_model)
    else:
        from app.providers.fake import FakeVision

        inner = FakeVision()
    return TrackedVision(inner, ledger, limiter)


def build_embedder(settings: Settings, ledger: CostLedger) -> TrackedEmbedder:
    limiter = RateLimiter(settings.embed_rpm)
    if settings.ai_provider == "gemini":
        from app.providers.gemini import GeminiEmbedder, make_client

        key = settings.gemini_api_key.get_secret_value() if settings.gemini_api_key else ""
        if not key:
            raise ProviderNotConfigured("GEMINI_API_KEY is not set. Copy .env.example to .env and add your key.")
        inner = GeminiEmbedder(make_client(key, settings.gemini_timeout_s), settings.gemini_embed_model,
                               settings.embed_dimensions)
    elif settings.ai_provider == "ollama":
        from app.providers.ollama import OllamaEmbedder

        client = httpx.Client(base_url=settings.ollama_url, timeout=settings.ollama_timeout_s)
        inner = OllamaEmbedder(client, settings.ollama_embed_model)
    else:
        from app.providers.fake import FakeEmbedder

        inner = FakeEmbedder()
    return TrackedEmbedder(inner, ledger, limiter)
