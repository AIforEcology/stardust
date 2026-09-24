"""A mock reforestation provider for development and tests.

It fulfills instantly with an obviously fake attestation. It is not a real project
and must never be listed in a production broker.

    uvicorn stardust_provider.examples.mock_reforestation:app --port 8081
"""

from __future__ import annotations

import threading
from typing import Dict, List, Optional

from ..interface import (
    Category,
    CatalogItem,
    Certificate,
    Certification,
    FulfillmentStatus,
    FulfillRequest,
    FulfillResult,
    ProviderInfo,
    QuoteResult,
    RemediationProvider,
)
from ..server import create_app

GRAMS_PER_TONNE = 1_000_000


class MockReforestationProvider(RemediationProvider):
    def __init__(self, provider_id: str = "mock-reforestation", price_usd_per_t: float = 20.0):
        self._provider_id = provider_id
        self._project = CatalogItem(
            project_id="mock-project-1",
            name="Mock reforestation project (NOT REAL)",
            region="nowhere",
            capacity_t_co2e=1_000,
            price_usd_per_t=price_usd_per_t,
        )
        self._orders: Dict[str, FulfillResult] = {}
        self._lock = threading.Lock()

    def register_provider(self) -> ProviderInfo:
        return ProviderInfo(
            provider_id=self._provider_id,
            name="Mock Reforestation (development only)",
            category=Category.reforestation,
            certification=Certification(standard="none"),
        )

    def get_catalog(self) -> List[CatalogItem]:
        return [self._project]

    def quote(self, co2e_g: float) -> QuoteResult:
        price = round(co2e_g / GRAMS_PER_TONNE * self._project.price_usd_per_t, 6)
        return QuoteResult(project_id=self._project.project_id, price_usd=price, fulfillment_days=0)

    def fulfill(self, order: FulfillRequest) -> FulfillResult:
        with self._lock:
            if order.order_id in self._orders:  # idempotent
                return self._orders[order.order_id]
            result = FulfillResult(
                order_id=order.order_id,
                fulfillment_status=FulfillmentStatus.fulfilled,
                certificate_ref=f"MOCK-ATTESTATION-{order.order_id}",
            )
            self._orders[order.order_id] = result
            return result

    def get_certificate(self, order_id: str) -> Optional[Certificate]:
        result = self._orders.get(order_id)
        if result is None or result.certificate_ref is None:
            return None
        return Certificate(order_id=order_id, certificate_ref=result.certificate_ref, kind="attestation", verified=False)

    def report_status(self, order_id: str) -> Optional[FulfillResult]:
        return self._orders.get(order_id)


app = create_app(MockReforestationProvider())
