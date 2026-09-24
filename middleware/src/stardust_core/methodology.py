"""Environmental methodology engine (spec §8) and event enrichment (§9.2).

v0.1 implements the Modeled and Estimated tiers with static provider/region
factors. The live grid feed (§8.6) and Measured-tier sources (§8.1) plug in here
later without changing the event shape.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from . import indicator
from .models import ConfidenceTier, EnrichedEvent, GridStyle, UsageEvent
from .pricing import PricingTable
from .sji import new_sji


@dataclass(frozen=True)
class Grid:
    key: str
    intensity_g_per_kwh: float
    mix: Optional[Dict[str, float]]


class Methodology:
    def __init__(self, cfg: Dict[str, Any], pricing: PricingTable):
        self.cfg = cfg
        self.version = cfg["version"]
        self.pricing = pricing
        self._tier_patterns = [(re.compile(p["pattern"], re.I), p["tier"]) for p in cfg["model_tiers"]["patterns"]]
        self._region_patterns = [(re.compile(p["pattern"], re.I), p["grid"]) for p in cfg["region_to_grid"]["patterns"]]

    # --- lookups -------------------------------------------------------------

    def model_tier(self, model: str) -> str:
        if model.strip().lower() in ("", "unknown"):
            return "unknown"
        for pattern, tier in self._tier_patterns:
            if pattern.search(model):
                return tier
        return "unknown"

    def grid_for(self, provider: str, region: Optional[str]) -> Grid:
        key = None
        if region:
            key = next((g for p, g in self._region_patterns if p.search(region)), self.cfg["region_to_grid"]["default"])
        else:
            defaults = self.cfg["provider_default_grid"]
            key = defaults.get(provider.lower(), defaults["default"])
        g = self.cfg["grids"][key]
        return Grid(key, float(g["intensity_g_per_kwh"]), g.get("mix"))

    # --- the engine ----------------------------------------------------------

    def energy_wh(self, event: UsageEvent, tier: str) -> Tuple[float, ConfidenceTier]:
        if event.tokens_in is None and event.tokens_out is None:
            return float(self.cfg["estimated_per_query_wh"]["value"]), ConfidenceTier.estimated

        factor_tier = tier if tier != "unknown" else self.cfg["model_tiers"]["unknown_tier_uses"]
        f = self.cfg["token_energy_wh"][factor_tier]
        wh = (event.tokens_in or 0) * f["input_per_token"] + (event.tokens_out or 0) * f["output_per_token"]

        # Modeled is the ceiling for token-derived energy: the tokens may be measured, the energy is not (§8.2).
        confidence = ConfidenceTier.estimated if (tier == "unknown" or event.tokens_estimated) else ConfidenceTier.modeled
        return wh, confidence

    def energy_source(self, grid: Grid) -> Tuple[str, Optional[float], bool]:
        """Plurality ESC of the grid mix, its share, and whether the mix is too diverse to call (§5.3.3)."""
        if not grid.mix:
            return "UNK", None, False
        code, share = max(grid.mix.items(), key=lambda kv: kv[1])
        threshold = self.cfg["grid_display"]["diversification_threshold_pct"]
        return code, float(share), share < threshold

    def enrich(self, event: UsageEvent) -> EnrichedEvent:
        tier = self.model_tier(event.model)
        energy_wh, confidence = self.energy_wh(event, tier)
        grid = self.grid_for(event.provider, event.region)

        co2e_g = energy_wh / 1000 * grid.intensity_g_per_kwh
        water = self.cfg["water"]
        # Wh → kWh (/1000) and L → mL (*1000) cancel out.
        water_ml = energy_wh * (water["onsite_wue_l_per_kwh"]["value"] + water["generation_l_per_kwh"]["value"])

        cost = self.pricing.cost_usd(event.provider, event.model, event.tokens_in, event.tokens_out, event.tokens_cached_in)
        esc, share, diversified = self.energy_source(grid)

        return EnrichedEvent(
            **event.model_dump(),
            job_sji=new_sji(esc),
            cost_usd=cost,
            energy_wh=energy_wh,
            grid_intensity_g_per_kwh=grid.intensity_g_per_kwh,
            co2e_g=co2e_g,
            water_ml=water_ml,
            confidence_tier=confidence,
            indicator_code=indicator.indicator_code(co2e_g, cost, event.total_tokens, self.cfg["indicator"]),
            energy_source_code=esc,
            energy_mix_breakdown=grid.mix,
            # Static factors are always a regional blend; "dedicated" needs PPA/on-site data (§12.9).
            grid_style=GridStyle.grid_blend,
            grid_majority_share_pct=share,
            grid_diversified=diversified,
            model_tier=tier,
            methodology_version=self.version,
        )
