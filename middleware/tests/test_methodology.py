from datetime import datetime, timezone

import pytest

from stardust_core.models import UsageEvent
from stardust_core.sji import esc_of


def ev(**kw):
    base = dict(source_layer="infra_agent", provider="anthropic", model="claude-test-opus",
                timestamp=datetime(2026, 9, 23, tzinfo=timezone.utc), tokens_in=500, tokens_out=500)
    base.update(kw)
    return UsageEvent(**base)


@pytest.mark.parametrize("model,tier", [
    ("claude-opus-4-1", "frontier"),
    ("claude-sonnet-4-5", "mid"),
    ("claude-haiku-4-5", "small"),
    ("gpt-4o", "frontier"),
    ("gpt-4o-mini", "small"),
    ("gemini-2.5-pro", "frontier"),
    ("gemini-2.5-flash", "small"),
    ("llama-3.1-70b", "frontier"),
    ("unknown", "unknown"),
    ("some-new-thing", "unknown"),
])
def test_model_tier(engine, model, tier):
    assert engine.model_tier(model) == tier


def test_frontier_us_worked_example(engine):
    # 500 in × 0.00025 + 500 out × 0.0006 = 0.425 Wh; × 367 g/kWh = 0.156 g
    e = engine.enrich(ev(region="us-east-1"))
    assert e.model_tier == "frontier"
    assert e.energy_wh == pytest.approx(0.425)
    assert e.co2e_g == pytest.approx(0.425 / 1000 * 367)
    assert e.water_ml == pytest.approx(0.425 * (1.8 + 3.14))
    assert e.cost_usd == pytest.approx(500 * 1e-05 + 500 * 5e-05)
    assert e.confidence_tier.value == "modeled"
    assert e.indicator_code == "C3-S"
    assert e.energy_source_code == "NGP"
    assert esc_of(e.job_sji) == "NGP"
    assert e.grid_style.value == "grid-blend"
    assert e.grid_majority_share_pct == 43
    assert e.grid_diversified is False
    assert e.methodology_version == "0.1.0"


def test_region_changes_impact_not_energy(engine):
    us = engine.enrich(ev(region="us-east-1"))
    se = engine.enrich(ev(region="eu-north-1"))
    fr = engine.enrich(ev(region="eu-west-3"))
    assert se.energy_wh == us.energy_wh
    assert se.co2e_g < fr.co2e_g < us.co2e_g
    assert se.energy_source_code == "HYD"
    assert fr.energy_source_code == "NPP"


def test_no_region_uses_provider_default(engine):
    assert engine.enrich(ev(provider="anthropic", region=None)).grid_intensity_g_per_kwh == 367
    assert engine.enrich(ev(provider="self-hosted", region=None)).grid_intensity_g_per_kwh == 480


def test_unmatched_region_is_global_with_unknown_source(engine):
    e = engine.enrich(ev(region="ap-southeast-2"))
    assert e.grid_intensity_g_per_kwh == 480
    assert e.energy_source_code == "UNK"
    assert e.grid_majority_share_pct is None


def test_unknown_model_is_estimated_and_conservative(engine):
    e = engine.enrich(ev(model="unknown", provider="openai"))
    assert e.confidence_tier.value == "estimated"
    assert e.energy_wh == pytest.approx(0.425)  # frontier factors
    assert e.cost_usd is None
    assert e.indicator_code[1] == "X"


def test_estimated_tokens_are_estimated(engine):
    assert engine.enrich(ev(tokens_estimated=True)).confidence_tier.value == "estimated"


def test_no_tokens_uses_per_query_floor(engine):
    e = engine.enrich(ev(tokens_in=None, tokens_out=None))
    assert e.energy_wh == pytest.approx(0.31)
    assert e.confidence_tier.value == "estimated"


def test_diversified_mix_flag(cfg, pricing_file):
    from stardust_core.methodology import Methodology
    from stardust_core.pricing import PricingTable

    cfg = {**cfg, "grid_display": {"diversification_threshold_pct": 50}}
    e = Methodology(cfg, PricingTable.load(pricing_file)).enrich(ev(region="us-east-1"))
    assert e.grid_diversified is True
    assert e.energy_source_code == "NGP"  # the field still records the plurality


@pytest.mark.parametrize("model,tier", [
    ("gpt-4o-2024-08-06", "frontier"),
    ("gpt-4.1-mini", "small"),
    ("o3-mini", "small"),
    ("o3", "frontier"),
    ("claude-3-5-haiku-20241022", "small"),
    ("llama-3.1-8b-instruct", "small"),
    ("mistral-small-latest", "small"),
    ("gemini-2.0-flash-lite", "small"),
])
def test_model_tier_edge_cases(engine, model, tier):
    assert engine.model_tier(model) == tier
