"""Alphanumeric indicator code ``[Impact Grade][Cost Tier]-[Token Volume Class]`` (spec §5.1).

Thresholds come from the methodology config. A cost tier of ``X`` means the cost
is unknown (no price for the model); this is a v0.1 extension pending the spec.
"""

from __future__ import annotations

from typing import Any, List, Optional, Sequence


def _band(value: float, table: Sequence[Sequence[Any]]) -> Any:
    for upper, label in table:
        if upper is None or value < upper:
            return label
    raise ValueError("threshold table must end with a null (catch-all) bound")


def impact_grade(co2e_g: float, total_tokens: Optional[int], cfg: dict) -> str:
    if total_tokens:
        return _band(co2e_g / total_tokens * 1000, cfg["impact_grade_by_co2e_g_per_1k_tokens"])
    return _band(co2e_g, cfg["impact_grade_by_co2e_g_no_tokens"])


def cost_tier(cost_usd: Optional[float], cfg: dict) -> str:
    if cost_usd is None:
        return "X"
    return str(_band(cost_usd, cfg["cost_tier_by_usd"]))


def volume_class(total_tokens: Optional[int], cfg: dict) -> str:
    return _band(total_tokens or 0, cfg["volume_class_by_total_tokens"])


def indicator_code(co2e_g: float, cost_usd: Optional[float], total_tokens: Optional[int], cfg: dict) -> str:
    return f"{impact_grade(co2e_g, total_tokens, cfg)}{cost_tier(cost_usd, cfg)}-{volume_class(total_tokens, cfg)}"


def aggregate_code(co2e_g: List[float], cost_usd: List[Optional[float]], tokens: List[Optional[int]], cfg: dict) -> Optional[str]:
    """Session/dashboard code (§5.2): grade on pooled g/1k tokens, cost and volume on the per-operation mean."""
    n = len(co2e_g)
    if n == 0:
        return None
    total_tokens = sum(t or 0 for t in tokens)
    known_costs = [c for c in cost_usd if c is not None]
    mean_cost = sum(known_costs) / len(known_costs) if known_costs else None
    # Without tokens, grade the mean operation so a long session isn't penalised for its length.
    grade = impact_grade(sum(co2e_g), total_tokens, cfg) if total_tokens else impact_grade(sum(co2e_g) / n, None, cfg)
    return (
        f"{grade}"
        f"{cost_tier(mean_cost, cfg)}-{volume_class(round(total_tokens / n), cfg)}"
    )
