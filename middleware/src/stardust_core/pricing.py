"""Token pricing from BerriAI/litellm's model_prices_and_context_window.json (MIT), spec §9.4.

The file is data, not code: it is schema-checked on load (§14.2) and entries that
don't carry sane per-token prices are dropped rather than trusted.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

from .config import load_json

log = logging.getLogger(__name__)

# litellm prefixes some keys with a routing provider; map Stardust provider slugs to them.
_PROVIDER_PREFIXES = {
    "anthropic": ["anthropic/"],
    "openai": ["openai/"],
    "google": ["gemini/", "vertex_ai/"],
}

# A per-token price above this is almost certainly a corrupted entry ($1,000 per 1k tokens).
_MAX_SANE_PRICE_PER_TOKEN = 1.0


@dataclass(frozen=True)
class Price:
    input_per_token: float
    output_per_token: float


class PricingTable:
    def __init__(self, prices: Dict[str, Price], source: str):
        self._prices = prices
        self.source = source

    def __len__(self) -> int:
        return len(self._prices)

    @classmethod
    def empty(cls) -> "PricingTable":
        return cls({}, source="none")

    @classmethod
    def load(cls, path: Path) -> "PricingTable":
        if not path.exists():
            log.warning("Pricing file %s not found; cost_usd will be null. Run scripts/update_pricing.py.", path)
            return cls.empty()
        raw = load_json(path)
        if not isinstance(raw, dict):
            raise ValueError(f"{path}: expected a JSON object keyed by model name")
        prices: Dict[str, Price] = {}
        for name, entry in raw.items():
            if name == "sample_spec" or not isinstance(entry, dict):
                continue
            p_in = entry.get("input_cost_per_token")
            p_out = entry.get("output_cost_per_token")
            if not (_is_price(p_in) and _is_price(p_out)):
                continue
            prices[name.lower()] = Price(float(p_in), float(p_out))
        log.info("Loaded %d priced models from %s", len(prices), path)
        return cls(prices, source=str(path))

    def lookup(self, provider: str, model: str) -> Optional[Price]:
        model = model.lower()
        if model in self._prices:
            return self._prices[model]
        for prefix in _PROVIDER_PREFIXES.get(provider.lower(), [f"{provider.lower()}/"]):
            hit = self._prices.get(prefix + model)
            if hit:
                return hit
        return None

    def cost_usd(self, provider: str, model: str, tokens_in: Optional[int], tokens_out: Optional[int]) -> Optional[float]:
        if tokens_in is None and tokens_out is None:
            return None
        price = self.lookup(provider, model)
        if price is None:
            return None
        return (tokens_in or 0) * price.input_per_token + (tokens_out or 0) * price.output_per_token


def _is_price(v: object) -> bool:
    return (
        isinstance(v, (int, float))
        and not isinstance(v, bool)
        and math.isfinite(v)
        and 0 <= v <= _MAX_SANE_PRICE_PER_TOKEN
    )
