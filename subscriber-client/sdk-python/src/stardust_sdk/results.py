"""Typed results for the reading and remediation helpers. Each mirrors a Core REST response.

Unknown fields Core adds later are kept in ``raw``, so a newer Core never breaks an older SDK.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any, Dict, Optional, Type, TypeVar

T = TypeVar("T")


class StardustError(Exception):
    """A user-initiated call to Core failed. ``status`` is the HTTP status (0: Core unreachable)."""

    def __init__(self, status: int, message: str, reason: Optional[str] = None):
        super().__init__(f"Stardust Core: {message}" + (f" (HTTP {status})" if status else ""))
        self.status, self.message, self.reason = status, message, reason


def _build(cls: Type[T], d: Dict[str, Any]) -> T:
    names = {f.name for f in fields(cls)} - {"raw"}  # type: ignore[arg-type]
    return cls(**{k: v for k, v in d.items() if k in names}, raw=dict(d))  # type: ignore[call-arg]


@dataclass(frozen=True)
class Summary:
    """``GET /v1/summary``: totals for the key's organization (or user)."""

    events: int
    tokens_in: int
    tokens_out: int
    cost_usd: float
    energy_wh: float
    co2e_g: float
    water_ml: float
    indicator_code: str
    methodology_version: str
    tokens_cached_in: int = 0
    cost_unknown_events: int = 0
    sandbox: bool = False
    raw: Dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Summary":
        return _build(cls, d)


@dataclass(frozen=True)
class DailySummary:
    """One UTC day from ``GET /v1/summary/daily``."""

    day: str
    events: int
    tokens_in: int
    tokens_out: int
    cost_usd: float
    energy_wh: float
    co2e_g: float
    water_ml: float
    raw: Dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "DailySummary":
        return _build(cls, d)


@dataclass(frozen=True)
class Quote:
    """A remediation quote. ``price_usd`` is the provider's price; AIforE's disclosed fee is separate."""

    quote_id: str
    provider_id: str
    project_id: str
    co2e_g: float
    price_usd: float
    fee_pct: float
    fee_usd: float
    total_usd: float
    fulfillment_days: int
    provider_tier: str
    expires_at: str
    sandbox: bool = False
    raw: Dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Quote":
        return _build(cls, d)


@dataclass(frozen=True)
class Certificate:
    ref: str
    sandbox: bool = False
    note: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Certificate":
        return _build(cls, d)


@dataclass(frozen=True)
class Order:
    """A remediation order. A sandbox order is a test: no carbon was removed and no money moved."""

    remediation_order_id: str
    fulfillment_status: str
    co2e_g_offset: float
    quote_id: Optional[str] = None
    provider_id: Optional[str] = None
    provider_tier: Optional[str] = None
    certificate_ref: Optional[str] = None
    certificate: Optional[Certificate] = None
    total_usd: Optional[float] = None
    fee_usd: Optional[float] = None
    sandbox: bool = False
    notice: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Order":
        d = dict(d)
        cert = d.get("certificate")
        order = _build(cls, {**d, "certificate": Certificate.from_dict(cert) if isinstance(cert, dict) else None})
        object.__setattr__(order, "raw", d)
        return order

    @property
    def fulfilled(self) -> bool:
        return self.fulfillment_status == "fulfilled"
