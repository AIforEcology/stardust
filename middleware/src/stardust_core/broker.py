"""Remediation Registry and Broker (spec §10.4).

The "print spooler": providers register once against the Provider interface
(§10.2, implemented in providers-service), subscribers talk only to the broker's
Subscriber API (§10.3). Providers reach the catalog only after AIforE vetting,
which assigns their tier (§10.2.1, §10.6).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

import httpx

from .fees import FeePolicy
from .store import Store

log = logging.getLogger(__name__)

TIERS = ("verified", "emerging", "engineered")
QUOTE_TTL = timedelta(minutes=15)


class BrokerError(Exception):
    """A request the broker refuses; message is safe to show a subscriber."""


@dataclass
class ProviderEntry:
    provider_id: str
    name: str
    category: str
    certification: dict
    base_url: str
    tier: Optional[str] = None  # None until vetted
    status: str = "pending"  # pending | approved | delisted

    @property
    def listed(self) -> bool:
        return self.status == "approved"


@dataclass
class Quote:
    quote_id: UUID
    provider_id: str
    project_id: str
    co2e_g: float
    price_usd: float
    fulfillment_days: int
    provider_tier: str
    expires_at: datetime
    # price_usd is the provider's price; AIforE's fee is shown separately (fees.py).
    fee_pct: float = 0.0
    fee_usd: float = 0.0
    total_usd: Optional[float] = None

    def __post_init__(self) -> None:
        if self.total_usd is None:
            self.total_usd = round(self.price_usd + self.fee_usd, 6)


@dataclass
class Order:
    remediation_order_id: UUID
    quote_id: Optional[UUID]
    provider_id: Optional[str]
    subscriber_id: str
    provider_tier: Optional[str]
    co2e_g_offset: float
    payment_ref: Optional[str]
    fulfillment_status: str = "ordered"
    certificate_ref: Optional[str] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    # Fixed from the quote at order time, so later fee changes never rewrite it.
    provider_price_usd: Optional[float] = None
    fee_pct: Optional[float] = None
    fee_usd: Optional[float] = None
    total_usd: Optional[float] = None


def _quote_from(d: Dict[str, Any]) -> Quote:
    return Quote(**{**d, "quote_id": UUID(d["quote_id"]), "expires_at": datetime.fromisoformat(d["expires_at"])})


def _order_from(d: Dict[str, Any]) -> Order:
    return Order(**{
        **d,
        "remediation_order_id": UUID(d["remediation_order_id"]),
        "quote_id": UUID(d["quote_id"]) if d.get("quote_id") else None,
        "created_at": datetime.fromisoformat(d["created_at"]),
    })


class Broker:
    """Providers are cached in memory (a small set, read on every quote); quotes and orders live in the store."""

    def __init__(self, http: httpx.AsyncClient, store: Store, fees: FeePolicy = FeePolicy()):
        self._http = http
        self._store = store
        self.fees = fees
        self.providers: Dict[str, ProviderEntry] = {d["provider_id"]: ProviderEntry(**d) for d in store.load_providers()}

    def _save_provider(self, entry: ProviderEntry) -> None:
        self._store.save_provider(entry.provider_id, entry.status, asdict(entry))

    def _save_order(self, order: Order) -> None:
        self._store.save_order(order.remediation_order_id, order.subscriber_id, order.fulfillment_status,
                               order.created_at, asdict(order), provider_price_usd=order.provider_price_usd,
                               fee_usd=order.fee_usd, total_usd=order.total_usd)

    # --- provider onboarding (admin) -----------------------------------------

    async def add_provider(self, base_url: str) -> ProviderEntry:
        """Fetch a provider's self-description (register_provider). It stays pending until approved."""
        base_url = base_url.rstrip("/")
        reg = await self._get(base_url, "/provider")
        entry = ProviderEntry(
            provider_id=reg["provider_id"],
            name=reg["name"],
            category=reg["category"],
            certification=reg.get("certification", {"standard": "none"}),
            base_url=base_url,
        )
        existing = self.providers.get(entry.provider_id)
        if existing and existing.base_url != base_url:
            raise BrokerError(f"provider_id {entry.provider_id!r} is already registered at another URL")
        self.providers[entry.provider_id] = entry
        self._save_provider(entry)
        return entry

    def approve(self, provider_id: str, tier: str) -> ProviderEntry:
        if tier not in TIERS:
            raise BrokerError(f"tier must be one of {TIERS}")
        entry = self._provider(provider_id)
        if tier == "verified" and entry.certification.get("standard") not in ("verra", "gold_standard"):
            # §10.6: Tier 1 requires an active Verra / Gold Standard registration.
            raise BrokerError("verified tier requires a Verra or Gold Standard certification")
        entry.tier, entry.status = tier, "approved"
        self._save_provider(entry)
        return entry

    def delist(self, provider_id: str) -> ProviderEntry:
        entry = self._provider(provider_id)
        entry.status = "delisted"
        self._save_provider(entry)
        return entry

    # --- Subscriber API (§10.3) ----------------------------------------------

    async def request_quote(self, co2e_g: float, tier_preference: Optional[List[str]] = None) -> List[Quote]:
        if co2e_g <= 0:
            raise BrokerError("co2e_g must be positive")
        candidates = [
            p for p in self.providers.values() if p.listed and (not tier_preference or p.tier in tier_preference)
        ]
        results = await asyncio.gather(*(self._quote_one(p, co2e_g) for p in candidates))
        quotes = [q for q in results if q is not None]
        quotes.sort(key=lambda q: q.total_usd or 0)
        return quotes

    async def submit_remediation_order(self, quote_id: UUID, subscriber_id: str, payment_ref: Optional[str]) -> Order:
        raw = self._store.take_quote(quote_id)  # atomic: a quote can be used once
        if raw is None:
            raise BrokerError("unknown or already-used quote")
        quote = _quote_from(raw)
        if quote.expires_at < datetime.now(timezone.utc):
            raise BrokerError("quote has expired; request a new one")
        provider = self._provider(quote.provider_id)
        if not provider.listed:
            raise BrokerError("provider is no longer listed")

        order = Order(
            remediation_order_id=uuid4(),
            quote_id=quote.quote_id,
            provider_id=provider.provider_id,
            subscriber_id=subscriber_id,
            provider_tier=provider.tier,  # recorded at order time (§10.5)
            co2e_g_offset=quote.co2e_g,
            payment_ref=payment_ref,
            provider_price_usd=quote.price_usd,
            fee_pct=quote.fee_pct,
            fee_usd=quote.fee_usd,
            total_usd=quote.total_usd,
        )
        try:
            result = await self._post(provider.base_url, "/fulfill", {
                "order_id": str(order.remediation_order_id),
                "project_id": quote.project_id,
                "co2e_g": quote.co2e_g,
                "price_usd": quote.price_usd,
                "payment_ref": payment_ref,
            })
            order.fulfillment_status = result.get("fulfillment_status", "fulfilling")
            order.certificate_ref = result.get("certificate_ref")
        except httpx.HTTPError as e:
            log.warning("fulfill failed at %s: %s", provider.provider_id, e)
            order.fulfillment_status = "failed"
        self._save_order(order)
        return order

    async def get_fulfillment_status(self, order_id: UUID) -> Order:
        raw = self._store.load_order(order_id)
        if raw is None:
            raise BrokerError("unknown order")
        order = _order_from(raw)
        if order.provider_id and order.fulfillment_status in ("ordered", "fulfilling"):
            provider = self._provider(order.provider_id)
            try:
                status = await self._get(provider.base_url, f"/status/{order_id}")
                order.fulfillment_status = status.get("fulfillment_status", order.fulfillment_status)
                order.certificate_ref = status.get("certificate_ref") or order.certificate_ref
            except httpx.HTTPError as e:
                log.warning("status poll failed at %s: %s", provider.provider_id, e)
            self._save_order(order)
        return order

    # --- internals ------------------------------------------------------------

    async def _quote_one(self, p: ProviderEntry, co2e_g: float) -> Optional[Quote]:
        try:
            r = await self._post(p.base_url, "/quote", {"co2e_g": co2e_g})
        except httpx.HTTPError as e:
            log.warning("quote failed at %s: %s", p.provider_id, e)
            return None
        price = float(r["price_usd"])
        q = Quote(
            quote_id=uuid4(),
            provider_id=p.provider_id,
            project_id=r["project_id"],
            co2e_g=co2e_g,
            price_usd=price,
            fee_pct=self.fees.pct,
            fee_usd=self.fees.fee_for(price),
            fulfillment_days=int(r["fulfillment_days"]),
            provider_tier=p.tier or "emerging",
            expires_at=datetime.now(timezone.utc) + QUOTE_TTL,
        )
        self._store.save_quote(q.quote_id, q.expires_at, asdict(q))
        return q

    def _provider(self, provider_id: str) -> ProviderEntry:
        entry = self.providers.get(provider_id)
        if entry is None:
            raise BrokerError(f"unknown provider {provider_id!r}")
        return entry

    async def _get(self, base_url: str, path: str) -> dict:
        r = await self._http.get(base_url + path, timeout=10)
        r.raise_for_status()
        return r.json()

    async def _post(self, base_url: str, path: str, body: dict) -> dict:
        r = await self._http.post(base_url + path, json=body, timeout=10)
        r.raise_for_status()
        return r.json()
