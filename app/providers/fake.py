"""Deterministic providers for tests and key-less smoke runs. Never used for real results."""
import hashlib
import json
from collections.abc import Iterable

from app.providers.base import EmbedResponse, Usage, VisionResponse


class FakeVision:
    """Replays a script of responses (str) or exceptions; then falls back to a fixed valid answer."""

    provider = "fake"
    model = "fake-vision-1"

    DEFAULT = json.dumps({
        "subject": "unidentified subject",
        "category": "other",
        "attributes": ["synthetic"],
        "caption": "A deterministic placeholder description from the fake provider.",
        "confidence": 0.5,
    })

    def __init__(self, script: Iterable[str | Exception] = ()):
        self.script = list(script)
        self.calls: list[str] = []

    def describe(self, image: bytes, mime_type: str, prompt: str, json_schema: dict) -> VisionResponse:
        self.calls.append(prompt)
        step = self.script.pop(0) if self.script else self.DEFAULT
        if isinstance(step, Exception):
            raise step
        return VisionResponse(text=step, usage=Usage(input_tokens=1000, output_tokens=100))


class FakeEmbedder:
    provider = "fake"
    model = "fake-embed-1"

    def __init__(self, dimensions: int = 16):
        self.dimensions = dimensions
        self.calls = 0

    def embed(self, texts: list[str]) -> EmbedResponse:
        self.calls += 1
        vectors = []
        for text in texts:
            digest = hashlib.sha256(text.encode()).digest()
            vectors.append([(digest[i % len(digest)] - 128) / 128 for i in range(self.dimensions)])
        return EmbedResponse(vectors=vectors, usage=Usage(input_tokens=sum(len(t) // 4 for t in texts)))
