"""The Remediation Provider Interface (spec §10.2).

Every provider — reforestation, blue carbon, DAC, biochar, mangrove/seagrass — implements
this one contract, the way a printer ships one driver for every application. The broker
in Stardust Core is the only caller; subscribers never talk to a provider directly.

Note that a provider does not declare its own confidence tier. The broker assigns it
during vetting (§10.2.1, §10.6).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class Category(str, Enum):
    reforestation = "reforestation"
    blue_carbon = "blue_carbon"
    dac = "dac"
    biochar = "biochar"
    mangrove_seagrass = "mangrove_seagrass"
    other = "other"


class Certification(BaseModel):
    standard: str = Field(description="verra | gold_standard | puro_earth | isometric | none")
    registry_id: Optional[str] = None


class ProviderInfo(BaseModel):
    provider_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,62}$")
    name: str
    category: Category
    certification: Certification


class CatalogItem(BaseModel):
    project_id: str
    name: str
    region: str
    capacity_t_co2e: float = Field(ge=0)
    price_usd_per_t: float = Field(ge=0)


class QuoteRequest(BaseModel):
    co2e_g: float = Field(gt=0)


class QuoteResult(BaseModel):
    project_id: str
    price_usd: float = Field(ge=0)
    fulfillment_days: int = Field(ge=0)


class FulfillRequest(BaseModel):
    order_id: str
    project_id: str
    co2e_g: float = Field(gt=0)
    price_usd: float = Field(ge=0)
    payment_ref: Optional[str] = None


class FulfillmentStatus(str, Enum):
    ordered = "ordered"
    fulfilling = "fulfilling"
    fulfilled = "fulfilled"
    failed = "failed"


class FulfillResult(BaseModel):
    order_id: str
    fulfillment_status: FulfillmentStatus
    certificate_ref: Optional[str] = Field(
        default=None,
        description="Verra/Gold Standard serial (verified tier) or a provider attestation reference.",
    )


class Certificate(BaseModel):
    order_id: str
    certificate_ref: str
    kind: str = Field(description="registry_serial | attestation")
    verified: bool


class RemediationProvider(ABC):
    """Implement these six methods, wrap with ``create_app()``, and hand the URL to AIforE for vetting."""

    @abstractmethod
    def register_provider(self) -> ProviderInfo:
        """Identity, category and certification status."""

    @abstractmethod
    def get_catalog(self) -> List[CatalogItem]:
        """Available projects: region, capacity, price."""

    @abstractmethod
    def quote(self, co2e_g: float) -> QuoteResult:
        """Cost and fulfillment time to sequester ``co2e_g`` grams of CO2e."""

    @abstractmethod
    def fulfill(self, order: FulfillRequest) -> FulfillResult:
        """Execute and record a purchase for a broker-routed order. Must be idempotent on ``order_id``."""

    @abstractmethod
    def get_certificate(self, order_id: str) -> Optional[Certificate]:
        """Proof of fulfillment, once available (``verify()`` in the spec)."""

    @abstractmethod
    def report_status(self, order_id: str) -> Optional[FulfillResult]:
        """Current status for orders that complete over time."""
