"""Provider-neutral interfaces. Services depend on these, never on a vendor SDK."""
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    estimated: bool = False  # True when the provider didn't report usage and we estimated it


@dataclass(frozen=True)
class VisionResponse:
    text: str
    usage: Usage


@dataclass(frozen=True)
class EmbedResponse:
    vectors: list[list[float]]
    usage: Usage


class ProviderError(Exception):
    """Base for provider failures. `retryable` tells the job worker whether to try again later."""

    retryable = False

    def __init__(self, message: str, *, retry_after_s: float | None = None):
        super().__init__(message)
        self.retry_after_s = retry_after_s


class TransientProviderError(ProviderError):
    """Timeouts, 5xx: worth retrying with backoff."""

    retryable = True


class RateLimitedError(TransientProviderError):
    """429 / quota exhausted: retry later (honouring retry_after_s when the provider gives one)."""


class PermanentProviderError(ProviderError):
    """Bad key, bad request, unknown model: retrying won't help."""


class ProviderNotConfigured(PermanentProviderError):
    """Settings are missing (e.g. no GEMINI_API_KEY)."""


class VisionProvider(Protocol):
    provider: str
    model: str

    def describe(self, image: bytes, mime_type: str, prompt: str, json_schema: dict) -> VisionResponse: ...

    def generate(self, prompt: str, json_schema: dict) -> VisionResponse:
        """Text-only structured output from the same model (used to read what a post is about)."""
        ...


class EmbeddingProvider(Protocol):
    provider: str
    model: str

    def embed(self, texts: list[str]) -> EmbedResponse: ...


def estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token) for providers that don't report usage."""
    return max(1, len(text) // 4)
