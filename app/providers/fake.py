"""Deterministic providers for tests and key-less smoke runs. Never used for real results."""
import hashlib
import json
import re
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

    TOPIC_DEFAULT = json.dumps({"subject": "unidentified subject", "category": "other", "confidence": 0.5})

    def __init__(self, script: Iterable[str | Exception] = (), topics: Iterable[str | Exception] = ()):
        self.script = list(script)        # answers for describe() (images)
        self.topics = list(topics)        # answers for generate() (posts)
        self.calls: list[str] = []        # vision prompts
        self.text_calls: list[str] = []   # text prompts

    def describe(self, image: bytes, mime_type: str, prompt: str, json_schema: dict) -> VisionResponse:
        self.calls.append(prompt)
        return self._answer(self.script.pop(0) if self.script else self.DEFAULT, 1000)

    def generate(self, prompt: str, json_schema: dict) -> VisionResponse:
        self.text_calls.append(prompt)
        return self._answer(self.topics.pop(0) if self.topics else self.TOPIC_DEFAULT, 300)

    @staticmethod
    def _answer(step: str | Exception, input_tokens: int) -> VisionResponse:
        if isinstance(step, Exception):
            raise step
        return VisionResponse(text=step, usage=Usage(input_tokens=input_tokens, output_tokens=100))


STOPWORDS = frozenset("the and with that this from for are its into over under".split())


class FakeEmbedder:
    """Bag-of-words vectors: texts that share words point in similar directions.

    Deterministic and offline, for tests and key-less smoke runs. It is NOT semantic: it cannot
    tell that "Vulpes vulpes" means fox. That proof uses the real model (EVIDENCE.md M-2).
    """

    provider = "fake"
    model = "fake-embed-1"

    def __init__(self, dimensions: int = 2048):
        self.dimensions = dimensions
        self.calls = 0

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for word in re.findall(r"[a-z]+", text.lower()):
            if len(word) > 2 and word not in STOPWORDS:
                bucket = int.from_bytes(hashlib.sha256(word.encode()).digest()[:4], "big") % (self.dimensions - 1)
                vector[bucket] += 1.0
        if not any(vector):
            vector[-1] = 1.0  # a text with no usable words still gets a valid, non-zero vector
        return vector

    def embed(self, texts: list[str]) -> EmbedResponse:
        self.calls += 1
        return EmbedResponse(vectors=[self._vector(t) for t in texts],
                             usage=Usage(input_tokens=sum(max(1, len(t) // 4) for t in texts)))
