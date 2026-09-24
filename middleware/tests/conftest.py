import json
from pathlib import Path

import pytest

from stardust_core.config import DEFAULT_METHODOLOGY, load_json
from stardust_core.methodology import Methodology
from stardust_core.pricing import PricingTable

# Test-only prices, deliberately round. Not real provider pricing.
TEST_PRICES = {
    "sample_spec": {"input_cost_per_token": 0, "output_cost_per_token": 0},
    "claude-test-opus": {"input_cost_per_token": 1e-05, "output_cost_per_token": 5e-05},
    "anthropic/claude-test-haiku": {"input_cost_per_token": 1e-06, "output_cost_per_token": 5e-06},
    "broken-model": {"input_cost_per_token": "free", "output_cost_per_token": 1e-06},
    "absurd-model": {"input_cost_per_token": 50.0, "output_cost_per_token": 1e-06},
}


@pytest.fixture
def pricing_file(tmp_path: Path) -> Path:
    p = tmp_path / "prices.json"
    p.write_text(json.dumps(TEST_PRICES))
    return p


@pytest.fixture
def cfg() -> dict:
    return load_json(DEFAULT_METHODOLOGY)


@pytest.fixture
def engine(cfg, pricing_file) -> Methodology:
    return Methodology(cfg, PricingTable.load(pricing_file))
