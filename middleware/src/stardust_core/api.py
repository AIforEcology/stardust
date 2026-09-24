"""Stardust Core HTTP API.

Run with ``uvicorn stardust_core.api:app``. Storage is in-memory in v0.1; events
and orders are lost on restart.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
from collections import OrderedDict, deque
from contextlib import asynccontextmanager
from typing import Deque, List, Optional, Tuple
from uuid import UUID

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from opentelemetry.sdk.trace.export import SpanExporter
from pydantic import BaseModel, Field

from . import indicator
from .broker import Broker, BrokerError
from .config import Settings, load_methodology
from .methodology import Methodology
from .models import EnrichedEvent, UsageEvent
from .otel import MAX_BODY_BYTES, ImpactSpanExporter, OtlpDecodeError, Parent, decode_request, encode_response, span_to_event
from .pricing_refresh import PricingRefresher, load_initial

log = logging.getLogger("stardust_core")
if not log.handlers:
    # uvicorn only configures its own loggers; make Core's (e.g. pricing refresh) visible too.
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(levelname)s:     [%(name)s] %(message)s"))
    log.addHandler(_handler)
    log.setLevel(logging.INFO)

MAX_STORED_EVENTS = 100_000


class QuoteRequest(BaseModel):
    co2e_g: float = Field(gt=0)
    tier_preference: Optional[List[str]] = None


class OrderRequest(BaseModel):
    quote_id: UUID
    subscriber_id: str = Field(min_length=1)
    payment_ref: Optional[str] = None


class DonationRequest(BaseModel):
    amount_usd: Optional[float] = Field(default=None, gt=0)


class AddProviderRequest(BaseModel):
    base_url: str


class ApproveRequest(BaseModel):
    tier: str


def _hex_id(value: str, length: int) -> bool:
    try:
        return len(value) == length and int(value, 16) != 0
    except ValueError:
        return False


def create_app(
    settings: Optional[Settings] = None,
    http: Optional[httpx.AsyncClient] = None,
    span_exporter: Optional[SpanExporter] = None,
) -> FastAPI:
    """``span_exporter`` overrides the OTLP exporter (tests pass an in-memory one; spans export synchronously)."""
    settings = settings or Settings.from_env()
    cfg = load_methodology(settings.methodology_path)
    engine = Methodology(cfg, load_initial(settings.pricing_path, settings.pricing_cache_path))
    events: Deque[EnrichedEvent] = deque(maxlen=MAX_STORED_EVENTS)
    # Recent events by id, so a client retry (SDK, extension or OTel collector) isn't counted twice.
    seen: "OrderedDict[UUID, EnrichedEvent]" = OrderedDict()

    otel_exporter: Optional[ImpactSpanExporter] = None
    if span_exporter is not None:
        otel_exporter = ImpactSpanExporter(span_exporter, engine.version, batch=False)
    elif settings.otlp_endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        otel_exporter = ImpactSpanExporter(
            OTLPSpanExporter(endpoint=settings.otlp_endpoint, headers=dict(settings.otlp_headers)), engine.version
        )
        log.info("Exporting Stardust spans to %s", settings.otlp_endpoint)

    def record(event: UsageEvent, parent: Optional[Parent] = None) -> Tuple[EnrichedEvent, bool]:
        """Enrich, store and export an event. Returns (enriched, is_new)."""
        existing = seen.get(event.event_id)
        if existing is not None:
            return existing, False
        enriched = engine.enrich(event)
        events.append(enriched)
        seen[enriched.event_id] = enriched
        if len(seen) > MAX_STORED_EVENTS:
            seen.popitem(last=False)
        if otel_exporter:
            otel_exporter.export(enriched, parent)
        return enriched, True
    owned_http = http is None
    client = http or httpx.AsyncClient()
    broker = Broker(client)
    refresher = PricingRefresher(
        get_table=lambda: engine.pricing,
        set_table=lambda t: setattr(engine, "pricing", t),
        http=client,
        url=settings.pricing_url.format(ref=settings.pricing_ref),
        cache_path=settings.pricing_cache_path,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        task = None
        if settings.pricing_refresh_hours is not None:
            task = asyncio.create_task(refresher.run(settings.pricing_refresh_hours))
        else:
            refresher.status["schedule"] = "off"
        yield
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        if owned_http:
            await client.aclose()
        if otel_exporter:
            otel_exporter.shutdown()

    app = FastAPI(title="Stardust Core", version="0.1.0", lifespan=lifespan)
    app.state.engine, app.state.broker, app.state.events = engine, broker, events
    app.state.pricing_refresher = refresher

    def require_admin(x_stardust_admin_token: Optional[str] = Header(default=None)) -> None:
        if not settings.admin_token:
            raise HTTPException(403, "admin endpoints are disabled (STARDUST_ADMIN_TOKEN not set)")
        if not x_stardust_admin_token or not hmac.compare_digest(x_stardust_admin_token, settings.admin_token):
            raise HTTPException(401, "invalid admin token")

    # --- metering -------------------------------------------------------------

    @app.get("/healthz")
    def healthz() -> dict:
        return {
            "ok": True,
            "methodology_version": engine.version,
            "priced_models": len(engine.pricing),
            "otlp_export": settings.otlp_endpoint if otel_exporter and span_exporter is None else None,
        }

    @app.get("/v1/pricing/status")
    def pricing_status() -> dict:
        return refresher.status

    @app.get("/v1/methodology")
    def methodology() -> dict:
        """The exact factor set in use, so every figure is traceable (§15 auditability)."""
        return cfg

    @app.post("/v1/events", response_model=EnrichedEvent)
    def ingest(event: UsageEvent) -> EnrichedEvent:
        return record(event)[0]

    @app.post("/v1/traces")
    async def otlp_traces(request: Request) -> Response:
        """OTLP/HTTP trace receiver (§11.3): GenAI spans become Stardust events; other spans are ignored."""
        content_type = request.headers.get("content-type", "application/x-protobuf")
        body = await request.body()
        if len(body) > MAX_BODY_BYTES:
            raise HTTPException(413, "payload too large")
        try:
            spans = decode_request(body, content_type, request.headers.get("content-encoding", ""))
        except OtlpDecodeError as e:
            raise HTTPException(400, str(e))

        rejected, errors = 0, []
        for span in spans:
            try:
                event = span_to_event(span)
            except ValueError as e:
                rejected += 1
                errors.append(f"{span.name or span.span_id}: {str(e).splitlines()[0]}")
                continue
            if event is None:
                continue
            parent = None
            if _hex_id(span.trace_id, 32) and _hex_id(span.span_id, 16):
                parent = Parent(span.trace_id, span.span_id, span.start_ns, span.end_ns)
            record(event, parent)

        payload, media_type = encode_response(content_type, rejected, "; ".join(errors[:5]))
        return Response(content=payload, media_type=media_type)

    @app.get("/v1/events", response_model=List[EnrichedEvent])
    def recent(user_id: Optional[UUID] = None, limit: int = Query(50, ge=1, le=1000)) -> List[EnrichedEvent]:
        matched = [e for e in reversed(events) if user_id is None or e.user_id == user_id]
        return matched[:limit]

    @app.get("/v1/summary")
    def summary(user_id: Optional[UUID] = None) -> dict:
        es = [e for e in events if user_id is None or e.user_id == user_id]
        costs = [e.cost_usd for e in es]
        return {
            "events": len(es),
            "tokens_in": sum(e.tokens_in or 0 for e in es),
            "tokens_out": sum(e.tokens_out or 0 for e in es),
            "cost_usd": sum(c for c in costs if c is not None),
            "cost_unknown_events": sum(1 for c in costs if c is None),
            "energy_wh": sum(e.energy_wh for e in es),
            "co2e_g": sum(e.co2e_g for e in es),
            "water_ml": sum(e.water_ml for e in es),
            "indicator_code": indicator.aggregate_code(
                [e.co2e_g for e in es], costs, [e.total_tokens for e in es], cfg["indicator"]
            ),
            "methodology_version": engine.version,
        }

    # --- Subscriber API (§10.3) -----------------------------------------------

    @app.post("/v1/remediation/quotes")
    async def request_quote(req: QuoteRequest) -> dict:
        try:
            quotes = await broker.request_quote(req.co2e_g, req.tier_preference)
        except BrokerError as e:
            raise HTTPException(400, str(e))
        return {"quotes": [q.__dict__ for q in quotes]}

    @app.post("/v1/remediation/orders")
    async def submit_order(req: OrderRequest) -> dict:
        try:
            order = await broker.submit_remediation_order(req.quote_id, req.subscriber_id, req.payment_ref)
        except BrokerError as e:
            raise HTTPException(400, str(e))
        return order.__dict__

    @app.get("/v1/remediation/orders/{order_id}")
    async def order_status(order_id: UUID) -> dict:
        try:
            return (await broker.get_fulfillment_status(order_id)).__dict__
        except BrokerError as e:
            raise HTTPException(404, str(e))

    @app.post("/v1/donations")
    def request_donation(_: DonationRequest) -> dict:
        # §18.2: Stardust never processes donations; it links out to AIforE's own channel.
        return {
            "recipient": "AIforE",
            "donation_url": settings.donation_url,
            "note": "A donation supports AIforE and the open standard. It is not an offset purchase.",
        }

    # --- provider vetting (admin, §10.6) --------------------------------------

    @app.get("/v1/providers")
    def list_providers() -> dict:
        return {"providers": [p.__dict__ for p in broker.providers.values() if p.listed]}

    @app.post("/v1/admin/providers", dependencies=[Depends(require_admin)])
    async def add_provider(req: AddProviderRequest) -> dict:
        try:
            return (await broker.add_provider(req.base_url)).__dict__
        except (BrokerError, httpx.HTTPError, KeyError) as e:
            raise HTTPException(400, f"could not register provider: {e}")

    @app.post("/v1/admin/providers/{provider_id}/approve", dependencies=[Depends(require_admin)])
    def approve(provider_id: str, req: ApproveRequest) -> dict:
        try:
            return broker.approve(provider_id, req.tier).__dict__
        except BrokerError as e:
            raise HTTPException(400, str(e))

    @app.post("/v1/admin/providers/{provider_id}/delist", dependencies=[Depends(require_admin)])
    def delist(provider_id: str) -> dict:
        try:
            return broker.delist(provider_id).__dict__
        except BrokerError as e:
            raise HTTPException(404, str(e))

    @app.post("/v1/admin/pricing/refresh", dependencies=[Depends(require_admin)])
    async def refresh_pricing() -> dict:
        return await refresher.refresh_once()

    return app


app = create_app()
