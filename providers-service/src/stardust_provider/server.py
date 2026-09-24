"""HTTP adapter exposing a RemediationProvider to the Stardust broker.

Wire contract (all JSON):

    GET  /provider                 -> ProviderInfo
    GET  /catalog                  -> [CatalogItem]
    POST /quote      {co2e_g}      -> QuoteResult
    POST /fulfill    FulfillRequest -> FulfillResult
    GET  /status/{order_id}        -> FulfillResult
    GET  /certificate/{order_id}   -> Certificate
    POST /telemetry  TelemetryRecord -> accepted record (carbon-capture providers, §20)
"""

from __future__ import annotations

from typing import List

from fastapi import FastAPI, HTTPException

from .interface import (
    CatalogItem,
    Certificate,
    FulfillRequest,
    FulfillResult,
    ProviderInfo,
    QuoteRequest,
    QuoteResult,
    RemediationProvider,
)
from .telemetry import TelemetryRecord


def create_app(provider: RemediationProvider) -> FastAPI:
    info = provider.register_provider()
    app = FastAPI(title=f"Stardust provider: {info.name}", version="0.1.0")

    @app.get("/provider", response_model=ProviderInfo)
    def register() -> ProviderInfo:
        return provider.register_provider()

    @app.get("/catalog", response_model=List[CatalogItem])
    def catalog() -> List[CatalogItem]:
        return provider.get_catalog()

    @app.post("/quote", response_model=QuoteResult)
    def quote(req: QuoteRequest) -> QuoteResult:
        return provider.quote(req.co2e_g)

    @app.post("/fulfill", response_model=FulfillResult)
    def fulfill(req: FulfillRequest) -> FulfillResult:
        return provider.fulfill(req)

    @app.get("/status/{order_id}", response_model=FulfillResult)
    def status(order_id: str) -> FulfillResult:
        result = provider.report_status(order_id)
        if result is None:
            raise HTTPException(404, "unknown order")
        return result

    @app.get("/certificate/{order_id}", response_model=Certificate)
    def certificate(order_id: str) -> Certificate:
        cert = provider.get_certificate(order_id)
        if cert is None:
            raise HTTPException(404, "no certificate yet")
        return cert

    @app.post("/telemetry", response_model=TelemetryRecord)
    def telemetry(record: TelemetryRecord) -> TelemetryRecord:
        # v0.1 validates only; forwarding to Stardust Core's telemetry store comes next.
        return record

    return app
