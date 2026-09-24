"""Stardust Core HTTP API.

Run with ``uvicorn stardust_core.main:app``. Data is kept in a SQLite file (see
docs/architecture/database.md); STARDUST_DATABASE_PATH chooses where.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import threading
from contextlib import asynccontextmanager
from datetime import datetime
from typing import List, Optional, Tuple
from uuid import UUID

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from opentelemetry.sdk.metrics.export import MetricReader
from opentelemetry.sdk.trace.export import SpanExporter
from pydantic import BaseModel, Field

from . import indicator
from .broker import Broker, BrokerError
from .config import Settings, load_methodology
from .methodology import Methodology
from .models import EnrichedEvent, UsageEvent
from .otel import (
    MAX_BODY_BYTES,
    OtlpDecodeError,
    OtlpSpan,
    build_telemetry,
    decode_request,
    encode_response,
    span_to_event,
    start_grpc_receiver,
)
from .pricing_refresh import PricingRefresher, load_initial
from .store import Store

log = logging.getLogger("stardust_core")
if not log.handlers:
    # uvicorn only configures its own loggers; make Core's (e.g. pricing refresh) visible too.
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(levelname)s:     [%(name)s] %(message)s"))
    log.addHandler(_handler)
    log.setLevel(logging.INFO)

# How often the retention purge and expired-quote cleanup run.
MAINTENANCE_INTERVAL_S = 6 * 3600


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


def create_app(
    settings: Optional[Settings] = None,
    http: Optional[httpx.AsyncClient] = None,
    span_exporter: Optional[SpanExporter] = None,
    metric_reader: Optional[MetricReader] = None,
) -> FastAPI:
    """``span_exporter`` / ``metric_reader`` replace the OTLP exporters (tests pass in-memory ones)."""
    settings = settings or Settings.from_env()
    cfg = load_methodology(settings.methodology_path)
    engine = Methodology(cfg, load_initial(settings.pricing_path, settings.pricing_cache_path))
    store = Store(settings.database_path or ":memory:")
    log.info("Database: %s", store.path)
    # Sync endpoints run in a thread pool and gRPC on the event loop; keep check-enrich-insert atomic
    # so a retried event (SDK, extension or OTel collector) is enriched and counted once.
    lock = threading.Lock()

    telemetry = build_telemetry(
        version=engine.version,
        endpoint=settings.otlp_endpoint,
        protocol=settings.otlp_protocol,
        headers=dict(settings.otlp_headers),
        signals=settings.otlp_signals,
        metrics_interval_s=settings.otlp_metrics_interval_s,
        span_exporter=span_exporter,
        metric_reader=metric_reader,
    )

    def record(event: UsageEvent) -> Tuple[EnrichedEvent, bool]:
        """Enrich, store and export an event. Returns (enriched, is_new)."""
        with lock:
            existing = store.get_event(event.event_id)
            if existing is not None:
                return existing, False
            enriched = engine.enrich(event)
            store.insert_event(enriched)
        telemetry.record(enriched)
        return enriched, True

    async def run_maintenance() -> None:
        """Retention purge (§14.1) and expired-quote cleanup: at startup, then every few hours."""
        while True:
            try:
                if settings.retention_days is not None:
                    purged = store.purge_events_before(store.retention_cutoff(settings.retention_days))
                    if purged:
                        log.info("Retention: deleted %d events older than %d days", purged, settings.retention_days)
                store.purge_expired_quotes()
            except Exception:  # noqa: BLE001 - maintenance must never take Core down
                log.exception("Database maintenance failed")
            await asyncio.sleep(MAINTENANCE_INTERVAL_S)

    def ingest_spans(spans: List[OtlpSpan]) -> Tuple[int, str]:
        """Shared by the HTTP and gRPC receivers. Returns (rejected spans, error message)."""
        rejected, errors = 0, []
        for span in spans:
            try:
                event = span_to_event(span)
            except ValueError as e:
                rejected += 1
                errors.append(f"{span.name or span.span_id}: {str(e).splitlines()[0]}")
                continue
            if event is not None:
                record(event)
        return rejected, "; ".join(errors[:5])

    owned_http = http is None
    client = http or httpx.AsyncClient()
    broker = Broker(client, store)
    refresher = PricingRefresher(
        get_table=lambda: engine.pricing,
        set_table=lambda t: setattr(engine, "pricing", t),
        http=client,
        url=settings.pricing_url.format(ref=settings.pricing_ref),
        cache_path=settings.pricing_cache_path,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        grpc_server = None
        if settings.otlp_grpc_listen:
            grpc_server, app.state.otlp_grpc_port = await start_grpc_receiver(settings.otlp_grpc_listen, ingest_spans)
        maintenance = asyncio.create_task(run_maintenance())
        task = None
        if settings.pricing_refresh_hours is not None:
            task = asyncio.create_task(refresher.run(settings.pricing_refresh_hours))
        else:
            refresher.status["schedule"] = "off"
        yield
        maintenance.cancel()
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        if grpc_server:
            await grpc_server.stop(grace=2)
        if owned_http:
            await client.aclose()
        telemetry.shutdown()
        store.close()

    app = FastAPI(title="Stardust Core", version="0.1.0", lifespan=lifespan)
    app.state.engine, app.state.broker, app.state.store = engine, broker, store
    app.state.pricing_refresher = refresher
    app.state.otlp_grpc_port = None

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
            "otlp_export": {
                "endpoint": settings.otlp_endpoint,
                "protocol": settings.otlp_protocol,
                "signals": list(settings.otlp_signals),
            } if settings.otlp_endpoint else None,
            "otlp_grpc_receiver": settings.otlp_grpc_listen,
            "database": store.stats(),
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

        rejected, message = ingest_spans(spans)
        payload, media_type = encode_response(content_type, rejected, message)
        return Response(content=payload, media_type=media_type)

    @app.get("/v1/events", response_model=List[EnrichedEvent])
    def recent(user_id: Optional[UUID] = None, limit: int = Query(50, ge=1, le=1000)) -> List[EnrichedEvent]:
        return store.recent_events(user_id=user_id, limit=limit)

    @app.get("/v1/summary")
    def summary(user_id: Optional[UUID] = None, org_id: Optional[UUID] = None,
                since: Optional[datetime] = None, until: Optional[datetime] = None) -> dict:
        """Totals for a user, an org, or everything, optionally within [since, until)."""
        a = store.aggregate(user_id=user_id, org_id=org_id, since=since, until=until)
        return {
            "events": a.events,
            "tokens_in": a.tokens_in,
            "tokens_out": a.tokens_out,
            "tokens_cached_in": a.tokens_cached_in,
            "cost_usd": a.cost_usd,
            "cost_unknown_events": a.events - a.cost_known_events,
            "energy_wh": a.energy_wh,
            "co2e_g": a.co2e_g,
            "water_ml": a.water_ml,
            "indicator_code": indicator.aggregate_code_from_totals(
                a.co2e_g, a.events, a.tokens_in + a.tokens_out, a.cost_usd, a.cost_known_events, cfg["indicator"]
            ),
            "methodology_version": engine.version,
        }

    @app.get("/v1/summary/daily")
    def summary_daily(user_id: Optional[UUID] = None, org_id: Optional[UUID] = None,
                      since: Optional[datetime] = None, until: Optional[datetime] = None) -> dict:
        """Per-day (UTC) totals for trend charts (§6.3)."""
        return {"days": store.daily(user_id=user_id, org_id=org_id, since=since, until=until)}

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

    @app.delete("/v1/admin/users/{user_id}/events", dependencies=[Depends(require_admin)])
    def delete_user_events(user_id: UUID) -> dict:
        """Erase one user's events (§14.1 deletion controls)."""
        with lock:
            deleted = store.delete_user_events(user_id)
        log.info("Deleted %d events for a user on admin request", deleted)
        return {"deleted": deleted}

    @app.post("/v1/admin/pricing/refresh", dependencies=[Depends(require_admin)])
    async def refresh_pricing() -> dict:
        return await refresher.refresh_once()

    return app

