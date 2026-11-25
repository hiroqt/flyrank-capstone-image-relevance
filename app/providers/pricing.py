"""Price book: turns token usage into a list-price-equivalent USD cost (config/pricing.yaml)."""
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import yaml

from app.providers.base import Usage

PER_MILLION = Decimal(1_000_000)


@dataclass(frozen=True)
class Price:
    input_per_m: Decimal
    output_per_m: Decimal
    tier: str

    def cost(self, usage: Usage) -> Decimal:
        total = (Decimal(usage.input_tokens) * self.input_per_m + Decimal(usage.output_tokens) * self.output_per_m)
        return (total / PER_MILLION).quantize(Decimal("0.00000001"))


class PriceBook:
    def __init__(self, config: dict):
        self._providers = config["providers"]

    @classmethod
    def from_file(cls, path: Path) -> "PriceBook":
        return cls(yaml.safe_load(path.read_text()))

    def price(self, provider: str, model: str) -> Price:
        entry = self._providers.get(provider)
        if entry is None:
            raise KeyError(f"No pricing configured for provider '{provider}'")
        rates = (entry.get("models") or {}).get(model) or entry["unknown_model"]
        return Price(Decimal(str(rates["input"])), Decimal(str(rates["output"])), entry["tier"])
