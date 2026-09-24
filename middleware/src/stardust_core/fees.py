"""Broker fee policy (resolves spec §19's open question on broker fees).

AIforE retains a disclosed percentage of each remediation order routed through the broker
to cover the cost of operating it (provider vetting, hosting, verification). The rest goes
to the provider. The rate is configuration (STARDUST_BROKER_FEE_PCT), and each quote and
order records the rate that applied, so changing it never rewrites past orders.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

# A transparent cost-recovery starting point: 92% of each order reaches the provider.
# Set it from actual operating costs once known (see the admin fee report's coverage figure).
DEFAULT_FEE_PCT = 8.0
MONTH_DAYS = 365.25 / 12
USD_PLACES = 6  # quotes can be fractions of a cent (pooled retail orders)


@dataclass(frozen=True)
class FeePolicy:
    pct: float = DEFAULT_FEE_PCT
    min_usd: float = 0.0

    def __post_init__(self) -> None:
        if not 0 <= self.pct <= 100:
            raise ValueError(f"broker fee must be between 0 and 100 percent, got {self.pct}")
        if self.min_usd < 0:
            raise ValueError(f"minimum broker fee can't be negative, got {self.min_usd}")

    def fee_for(self, provider_price_usd: float) -> float:
        """AIforE's fee on top of a provider's price. Nothing is charged on a free quote."""
        if provider_price_usd <= 0:
            return 0.0
        return round(max(provider_price_usd * self.pct / 100, self.min_usd), USD_PLACES)

    def terms(self) -> Dict[str, Any]:
        """Public disclosure of the fee, for subscribers to show their users."""
        return {
            "fee_pct": self.pct,
            "min_fee_usd": self.min_usd,
            "recipient": "AIforE",
            "purpose": "Covers the cost of operating the remediation broker: provider vetting, hosting and verification.",
            "applies_to": "Remediation orders routed through the broker. Not donations, which go to AIforE directly.",
            "rate_locking": "Each quote shows the provider price, the fee and the total. The fee on a quote is the one charged.",
        }


def coverage(fees_usd: float, monthly_operating_cost_usd: float, days: float) -> Dict[str, float]:
    """How much of the operating budget for a period of `days` the fees covered."""
    budget = monthly_operating_cost_usd * days / MONTH_DAYS
    return {
        "operating_cost_usd": round(budget, 2),
        "coverage_pct": round(fees_usd / budget * 100, 1) if budget > 0 else 0.0,
    }
