"""Gemini (Google AI Studio, free tier) through the official google-genai SDK."""
import logging
from typing import Any

import httpx

from app.providers.base import (
    EmbedResponse,
    PermanentProviderError,
    ProviderError,
    RateLimitedError,
    TransientProviderError,
    Usage,
    VisionResponse,
    estimate_tokens,
)

log = logging.getLogger(__name__)


def make_client(api_key: str, timeout_s: float) -> Any:
    from google import genai
    from google.genai import types

    # SDK-level retries are off: the job worker owns retries/backoff so every attempt is cost-logged.
    return genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(timeout=int(timeout_s * 1000), retry_options=types.HttpRetryOptions(attempts=1)),
    )


def map_error(exc: Exception) -> ProviderError:
    """Translate SDK/transport errors into retryable vs permanent provider errors."""
    from google.genai import errors

    if isinstance(exc, errors.APIError):
        code = exc.code or 0
        message = f"Gemini API {code}: {exc.message or exc.status or 'error'}"
        if code == 429:
            return RateLimitedError(message, retry_after_s=_retry_delay(exc))
        if code in (408, 409) or code >= 500:
            return TransientProviderError(message)
        return PermanentProviderError(message)
    if isinstance(exc, (httpx.TimeoutException, httpx.TransportError, TimeoutError, ConnectionError)):
        return TransientProviderError(f"Gemini transport error: {type(exc).__name__}")
    return TransientProviderError(f"Gemini unexpected error: {type(exc).__name__}: {exc}")


def _retry_delay(exc: Any) -> float | None:
    """Read RetryInfo.retryDelay (e.g. '17s') from a 429 body when present."""
    try:
        for detail in (exc.details or {}).get("error", {}).get("details", []):
            delay = detail.get("retryDelay")
            if isinstance(delay, str) and delay.endswith("s"):
                return float(delay[:-1])
    except (AttributeError, TypeError, ValueError):
        pass
    return None


class GeminiVision:
    provider = "gemini"

    def __init__(self, client: Any, model: str):
        self._client = client
        self.model = model

    def describe(self, image: bytes, mime_type: str, prompt: str, json_schema: dict) -> VisionResponse:
        from google.genai import types

        try:
            response = self._client.models.generate_content(
                model=self.model,
                contents=[types.Part.from_bytes(data=image, mime_type=mime_type), prompt],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_json_schema=json_schema,
                    temperature=0.2,
                ),
            )
        except Exception as exc:  # noqa: BLE001 (mapped into our error taxonomy)
            raise map_error(exc) from exc

        meta = response.usage_metadata
        usage = Usage(
            input_tokens=(meta.prompt_token_count or 0) if meta else 0,
            # Output is billed including thinking tokens.
            output_tokens=((meta.candidates_token_count or 0) + (meta.thoughts_token_count or 0)) if meta else 0,
            estimated=meta is None,
        )
        return VisionResponse(text=response.text or "", usage=usage)


class GeminiEmbedder:
    provider = "gemini"

    def __init__(self, client: Any, model: str, dimensions: int):
        self._client = client
        self.model = model
        self.dimensions = dimensions

    def embed(self, texts: list[str]) -> EmbedResponse:
        from google.genai import types

        try:
            response = self._client.models.embed_content(
                model=self.model,
                contents=texts,
                config=types.EmbedContentConfig(
                    task_type="SEMANTIC_SIMILARITY", output_dimensionality=self.dimensions
                ),
            )
        except Exception as exc:  # noqa: BLE001
            raise map_error(exc) from exc

        vectors = [list(e.values or []) for e in (response.embeddings or [])]
        if len(vectors) != len(texts) or any(not v for v in vectors):
            raise PermanentProviderError("Gemini returned a malformed embedding response")
        # The embeddings endpoint doesn't report token usage, so estimate it (flagged as estimated).
        usage = Usage(input_tokens=sum(estimate_tokens(t) for t in texts), output_tokens=0, estimated=True)
        return EmbedResponse(vectors=vectors, usage=usage)
