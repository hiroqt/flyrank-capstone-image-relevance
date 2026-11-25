"""Ollama: the fully local, no-key path (brief section 10)."""
import base64

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


def _map(exc: Exception) -> ProviderError:
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code == 429:
            return RateLimitedError(f"Ollama {code}")
        if code >= 500:
            return TransientProviderError(f"Ollama {code}: {exc.response.text[:200]}")
        return PermanentProviderError(f"Ollama {code}: {exc.response.text[:200]}")
    if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
        return TransientProviderError(f"Ollama transport error: {type(exc).__name__}")
    return TransientProviderError(f"Ollama unexpected error: {type(exc).__name__}")


class OllamaVision:
    provider = "ollama"

    def __init__(self, client: httpx.Client, model: str):
        self._client = client
        self.model = model

    def describe(self, image: bytes, mime_type: str, prompt: str, json_schema: dict) -> VisionResponse:
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt, "images": [base64.b64encode(image).decode()]}],
            "format": json_schema,
            "stream": False,
            "options": {"temperature": 0.2},
        }
        try:
            response = self._client.post("/api/chat", json=payload)
            response.raise_for_status()
            body = response.json()
        except Exception as exc:  # noqa: BLE001
            raise _map(exc) from exc
        text = (body.get("message") or {}).get("content", "")
        usage = Usage(input_tokens=body.get("prompt_eval_count", 0), output_tokens=body.get("eval_count", 0))
        return VisionResponse(text=text, usage=usage)


class OllamaEmbedder:
    provider = "ollama"

    def __init__(self, client: httpx.Client, model: str):
        self._client = client
        self.model = model

    def embed(self, texts: list[str]) -> EmbedResponse:
        try:
            response = self._client.post("/api/embed", json={"model": self.model, "input": texts})
            response.raise_for_status()
            body = response.json()
        except Exception as exc:  # noqa: BLE001
            raise _map(exc) from exc
        vectors = body.get("embeddings") or []
        if len(vectors) != len(texts):
            raise PermanentProviderError("Ollama returned a malformed embedding response")
        tokens = body.get("prompt_eval_count")
        usage = Usage(
            input_tokens=tokens if tokens is not None else sum(estimate_tokens(t) for t in texts),
            estimated=tokens is None,
        )
        return EmbedResponse(vectors=vectors, usage=usage)
