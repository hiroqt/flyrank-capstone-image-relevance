"""Provider adapters, pricing, rate limiting and backoff. No network calls."""
import base64
import json
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest
from google.genai import errors

from app.config import PROJECT_ROOT
from app.jobs.retry import RateLimiter, backoff_seconds
from app.providers.base import PermanentProviderError, RateLimitedError, TransientProviderError, Usage
from app.providers.gemini import GeminiEmbedder, GeminiVision, map_error
from app.providers.ollama import OllamaVision
from app.providers.pricing import PriceBook

PRICES = PriceBook.from_file(PROJECT_ROOT / "config/pricing.yaml")


def test_cost_uses_list_price_per_million_tokens():
    price = PRICES.price("gemini", "gemini-3.5-flash-lite")
    assert price.tier == "free"
    # 1000 in * $0.30/M + 200 out * $2.50/M = $0.0003 + $0.0005
    assert price.cost(Usage(input_tokens=1000, output_tokens=200)) == Decimal("0.00080000")


def test_unlisted_model_is_never_priced_at_zero():
    assert PRICES.price("gemini", "gemini-99-ultra").cost(Usage(1000, 1000)) > 0


def test_local_models_cost_nothing():
    price = PRICES.price("ollama", "moondream")
    assert (price.tier, price.cost(Usage(5000, 5000))) == ("local", Decimal("0E-8"))


def test_rate_limiter_spaces_calls():
    now, sleeps = [0.0], []

    def sleep(s):
        sleeps.append(s)
        now[0] += s

    limiter = RateLimiter(60, clock=lambda: now[0], sleep=sleep)
    for _ in range(3):
        limiter.acquire()
    assert sleeps == [1.0, 1.0]


@pytest.mark.parametrize("attempt, rand, expected", [(1, 0.0, 1.0), (1, 1.0, 2.0), (3, 0.0, 4.0), (3, 1.0, 8.0)])
def test_backoff_is_exponential_with_jitter(attempt, rand, expected):
    assert backoff_seconds(attempt, base=2, cap=60, rand=lambda: rand) == expected


def test_backoff_is_capped_and_honours_retry_after():
    assert backoff_seconds(10, base=2, cap=5, rand=lambda: 1.0) == 5
    assert backoff_seconds(1, base=2, cap=60, retry_after=30, rand=lambda: 0.0) == 30


def _api_error(code, status="ERROR", details=None):
    body = {"error": {"code": code, "message": "boom", "status": status, "details": details or []}}
    return errors.APIError(code, body)


def test_gemini_429_is_rate_limited_with_retry_delay():
    err = map_error(_api_error(429, "RESOURCE_EXHAUSTED",
                               [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "17s"}]))
    assert isinstance(err, RateLimitedError) and err.retry_after_s == 17.0


@pytest.mark.parametrize("exc, kind", [
    (_api_error(503), TransientProviderError),
    (_api_error(500), TransientProviderError),
    (_api_error(400), PermanentProviderError),
    (_api_error(403), PermanentProviderError),
    (httpx.ReadTimeout("slow"), TransientProviderError),
])
def test_gemini_errors_map_to_retryable_or_permanent(exc, kind):
    assert type(map_error(exc)) is kind


def test_gemini_vision_sends_image_and_schema_and_counts_thinking_tokens():
    seen = {}

    def generate_content(model, contents, config):
        seen.update(model=model, contents=contents, config=config)
        usage = SimpleNamespace(prompt_token_count=1290, candidates_token_count=80, thoughts_token_count=40)
        return SimpleNamespace(text='{"ok": true}', usage_metadata=usage)

    client = SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    schema = {"type": "object"}
    result = GeminiVision(client, "gemini-3.5-flash-lite").describe(b"\xff\xd8jpeg", "image/jpeg", "describe", schema)

    assert result.text == '{"ok": true}'
    assert (result.usage.input_tokens, result.usage.output_tokens) == (1290, 120)
    assert seen["model"] == "gemini-3.5-flash-lite"
    assert seen["config"].response_json_schema == schema
    assert seen["config"].response_mime_type == "application/json"
    assert seen["contents"][0].inline_data.mime_type == "image/jpeg"


def test_gemini_vision_maps_sdk_errors():
    def generate_content(**_):
        raise _api_error(429, "RESOURCE_EXHAUSTED")

    client = SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    with pytest.raises(RateLimitedError):
        GeminiVision(client, "m").describe(b"x", "image/jpeg", "p", {})


def test_gemini_embedder_uses_semantic_similarity_task():
    seen = {}

    def embed_content(model, contents, config):
        seen.update(config=config)
        return SimpleNamespace(embeddings=[SimpleNamespace(values=[0.1, 0.2]) for _ in contents])

    client = SimpleNamespace(models=SimpleNamespace(embed_content=embed_content))
    result = GeminiEmbedder(client, "gemini-embedding-001", 768).embed(["red fox", "Vulpes vulpes"])
    assert result.vectors == [[0.1, 0.2], [0.1, 0.2]]
    assert seen["config"].task_type == "SEMANTIC_SIMILARITY"
    assert seen["config"].output_dimensionality == 768
    assert result.usage.estimated is True


def test_ollama_vision_request_and_usage():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"message": {"content": '{"ok": 1}'},
                                         "prompt_eval_count": 700, "eval_count": 60})

    client = httpx.Client(base_url="http://ollama", transport=httpx.MockTransport(handler))
    schema = {"type": "object"}
    result = OllamaVision(client, "moondream").describe(b"img", "image/jpeg", "describe", schema)

    assert result.text == '{"ok": 1}'
    assert (result.usage.input_tokens, result.usage.output_tokens) == (700, 60)
    assert captured["format"] == schema and captured["stream"] is False
    assert captured["messages"][0]["images"] == [base64.b64encode(b"img").decode()]


def test_ollama_server_error_is_transient():
    client = httpx.Client(base_url="http://ollama",
                          transport=httpx.MockTransport(lambda r: httpx.Response(503, text="loading model")))
    with pytest.raises(TransientProviderError):
        OllamaVision(client, "moondream").describe(b"img", "image/jpeg", "p", {})
