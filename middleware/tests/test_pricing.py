from pathlib import Path

import pytest

from stardust_core.pricing import PricingTable


def test_lookup_direct_and_prefixed(pricing_file):
    t = PricingTable.load(pricing_file)
    assert t.lookup("anthropic", "claude-test-opus").output_per_token == 5e-05
    assert t.lookup("anthropic", "CLAUDE-TEST-HAIKU").input_per_token == 1e-06  # via anthropic/ prefix
    assert t.lookup("anthropic", "claude-test-legacy").input_per_token == 2e-05  # via dated Bedrock key
    assert t.lookup("openai", "claude-test-haiku") is None


def test_invalid_entries_are_dropped(pricing_file):
    t = PricingTable.load(pricing_file)
    assert t.lookup("x", "broken-model") is None
    assert t.lookup("x", "absurd-model") is None
    assert t.lookup("x", "sample_spec") is None
    assert len(t) == 3


def test_cost(pricing_file):
    t = PricingTable.load(pricing_file)
    assert t.cost_usd("anthropic", "claude-test-opus", 1000, 100) == pytest.approx(0.015)
    assert t.cost_usd("anthropic", "claude-test-opus", None, None) is None
    assert t.cost_usd("anthropic", "not-priced", 10, 10) is None


def test_missing_file_is_empty(tmp_path: Path):
    assert len(PricingTable.load(tmp_path / "nope.json")) == 0


def test_non_object_file_rejected(tmp_path: Path):
    p = tmp_path / "list.json"
    p.write_text("[1, 2]")
    with pytest.raises(ValueError):
        PricingTable.load(p)
