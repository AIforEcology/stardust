"""OpenTelemetry harmonization (spec §11).

Receiver: OTLP trace payloads over HTTP (protobuf or JSON) or gRPC. Spans carrying GenAI usage
(``gen_ai.usage.*``) become Stardust usage events, keyed deterministically by
trace/span id so collector retries don't double count.

Exporter (OTLP over HTTP or gRPC):

- traces: each enriched event becomes a ``stardust.impact`` span with the usage side
  under ``gen_ai.*`` (§11.1) and everything environmental under ``stardust.*`` (§11.2).
  When the event carries trace context (it came in over OTLP, or from an SDK with
  OpenTelemetry on), the span is a child of the original GenAI span.
- metrics: running totals (requests, tokens, cost, energy, CO2e, water) with
  low-cardinality attributes, for dashboards (§6.3, §11.3).

Stardust's own spans are never re-ingested, so Core can safely export into a
collector pipeline that also feeds its receiver.
"""

from __future__ import annotations

import gzip
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

import grpc
from opentelemetry import trace
from opentelemetry.proto.collector.trace.v1 import trace_service_pb2_grpc
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
    ExportTraceServiceResponse,
)
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import MetricReader, PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor, SpanExporter
from opentelemetry.trace import NonRecordingSpan, SpanContext, SpanKind, TraceFlags

from .models import EnrichedEvent, SourceLayer, UsageEvent

log = logging.getLogger(__name__)

SERVICE_NAME = "stardust-core"
SPAN_NAME = "stardust.impact"
# Namespace for deriving event_id from (trace_id, span_id); fixed forever so ids stay stable.
EVENT_ID_NAMESPACE = uuid.UUID("5a2b7f0e-3c1d-4e8a-9b6f-2d4c8e1a7f30")

# --- attribute names ---------------------------------------------------------------

GEN_AI_PROVIDER = "gen_ai.provider.name"
GEN_AI_SYSTEM = "gen_ai.system"  # older semconv name for the provider
GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
GEN_AI_RESPONSE_MODEL = "gen_ai.response.model"
GEN_AI_INPUT_TOKENS = "gen_ai.usage.input_tokens"
GEN_AI_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
GEN_AI_CACHE_READ = "gen_ai.usage.cache_read.input_tokens"
GEN_AI_CACHE_WRITE = "gen_ai.usage.cache_creation.input_tokens"
CLOUD_REGION = "cloud.region"
SERVICE_NAME_ATTR = "service.name"

STARDUST_EVENT_ID = "stardust.event_id"
STARDUST_SOURCE_LAYER = "stardust.source_layer"
STARDUST_USER_ID = "stardust.user_id"
STARDUST_ORG_ID = "stardust.org_id"
STARDUST_ERF = "stardust.facility.energy_reuse_factor"

# gen_ai.provider.name / gen_ai.system values → Stardust provider slugs.
_PROVIDER_ALIASES = {
    "gcp.gemini": "google",
    "gcp.vertex_ai": "google",
    "gcp.gen_ai": "google",
    "gemini": "google",
    "vertex_ai": "google",
    "azure.ai.openai": "openai",
    "az.ai.openai": "openai",
}

MAX_BODY_BYTES = 10 * 1024 * 1024


class OtlpDecodeError(ValueError):
    """The request body isn't a valid OTLP trace payload."""


@dataclass
class OtlpSpan:
    trace_id: str  # 32 hex chars
    span_id: str  # 16 hex chars
    name: str
    start_ns: int
    end_ns: int
    attributes: Dict[str, Any] = field(default_factory=dict)
    resource: Dict[str, Any] = field(default_factory=dict)


# --- decoding ----------------------------------------------------------------------


def _json_value(v: Dict[str, Any]) -> Any:
    if "stringValue" in v:
        return v["stringValue"]
    if "intValue" in v:
        return int(v["intValue"])  # int64 is a string in OTLP JSON
    if "doubleValue" in v:
        return float(v["doubleValue"])
    if "boolValue" in v:
        return bool(v["boolValue"])
    if "arrayValue" in v:
        return [_json_value(x) for x in v["arrayValue"].get("values", [])]
    if "kvlistValue" in v:
        return _json_attrs(v["kvlistValue"].get("values", []))
    return None


def _json_attrs(kvs: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    return {kv["key"]: _json_value(kv.get("value", {})) for kv in kvs if "key" in kv}


def decode_json(body: bytes) -> List[OtlpSpan]:
    try:
        payload = json.loads(body)
        spans = []
        for rs in payload.get("resourceSpans", []):
            resource = _json_attrs(rs.get("resource", {}).get("attributes", []))
            for ss in rs.get("scopeSpans", []):
                for s in ss.get("spans", []):
                    spans.append(OtlpSpan(
                        trace_id=str(s.get("traceId", "")).lower(),
                        span_id=str(s.get("spanId", "")).lower(),
                        name=s.get("name", ""),
                        start_ns=int(s.get("startTimeUnixNano", 0) or 0),
                        end_ns=int(s.get("endTimeUnixNano", 0) or 0),
                        attributes=_json_attrs(s.get("attributes", [])),
                        resource=resource,
                    ))
        return spans
    except (ValueError, TypeError, AttributeError, KeyError) as e:
        raise OtlpDecodeError(f"invalid OTLP JSON: {e}") from e


def _pb_value(v: Any) -> Any:
    kind = v.WhichOneof("value")
    if kind == "array_value":
        return [_pb_value(x) for x in v.array_value.values]
    if kind == "kvlist_value":
        return {kv.key: _pb_value(kv.value) for kv in v.kvlist_value.values}
    return getattr(v, kind) if kind else None


def decode_protobuf(body: bytes) -> List[OtlpSpan]:
    req = ExportTraceServiceRequest()
    try:
        req.ParseFromString(body)
    except Exception as e:  # noqa: BLE001 - protobuf raises its own DecodeError
        raise OtlpDecodeError(f"invalid OTLP protobuf: {e}") from e
    return spans_from_request(req)


def spans_from_request(req: ExportTraceServiceRequest) -> List[OtlpSpan]:
    spans = []
    for rs in req.resource_spans:
        resource = {kv.key: _pb_value(kv.value) for kv in rs.resource.attributes}
        for ss in rs.scope_spans:
            for s in ss.spans:
                spans.append(OtlpSpan(
                    trace_id=s.trace_id.hex(),
                    span_id=s.span_id.hex(),
                    name=s.name,
                    start_ns=s.start_time_unix_nano,
                    end_ns=s.end_time_unix_nano,
                    attributes={kv.key: _pb_value(kv.value) for kv in s.attributes},
                    resource=resource,
                ))
    return spans


def decode_request(body: bytes, content_type: str, content_encoding: str) -> List[OtlpSpan]:
    if content_encoding.strip().lower() == "gzip":
        try:
            body = gzip.decompress(body)
        except OSError as e:
            raise OtlpDecodeError(f"invalid gzip body: {e}") from e
        if len(body) > MAX_BODY_BYTES:
            raise OtlpDecodeError("decompressed body too large")
    if "json" in content_type.lower():
        return decode_json(body)
    return decode_protobuf(body)


def encode_response(content_type: str, rejected: int, message: str) -> Tuple[bytes, str]:
    """ExportTraceServiceResponse in the request's encoding, with partial_success when needed."""
    if "json" in content_type.lower():
        payload: Dict[str, Any] = {}
        if rejected:
            payload["partialSuccess"] = {"rejectedSpans": str(rejected), "errorMessage": message}
        return json.dumps(payload).encode(), "application/json"
    resp = ExportTraceServiceResponse()
    if rejected:
        resp.partial_success.rejected_spans = rejected
        resp.partial_success.error_message = message
    return resp.SerializeToString(), "application/x-protobuf"


# --- span → usage event --------------------------------------------------------------


def valid_hex_id(value: Optional[str], length: int) -> bool:
    try:
        return value is not None and len(value) == length and int(value, 16) != 0
    except ValueError:
        return False


def event_id_for_span(trace_id: str, span_id: str) -> uuid.UUID:
    """Deterministic event id for a span. The SDKs derive the same id, so an event that reaches
    Core both directly and through a collector is counted once."""
    return uuid.uuid5(EVENT_ID_NAMESPACE, f"{trace_id}:{span_id}")


def _token(v: Any) -> Optional[int]:
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float, str)):
        raise ValueError(f"token count must be a number, got {v!r}")
    return int(v)


def _uuid_or_none(v: Any) -> Optional[uuid.UUID]:
    try:
        return uuid.UUID(str(v)) if v is not None else None
    except ValueError:
        return None


def _erf(v: Any) -> Optional[float]:
    """A reported Energy Reuse Factor, if it's a number between 0 and 1; otherwise ignored."""
    try:
        f = float(v) if v is not None and not isinstance(v, bool) else None
    except (TypeError, ValueError):
        return None
    return f if f is not None and 0 <= f <= 1 else None


def is_stardust_span(span: OtlpSpan) -> bool:
    return (
        STARDUST_EVENT_ID in span.attributes
        or span.resource.get(SERVICE_NAME_ATTR) == SERVICE_NAME
        or span.name == SPAN_NAME
    )


def span_to_event(span: OtlpSpan) -> Optional[UsageEvent]:
    """A usage event for a GenAI span, or None for spans that aren't GenAI usage.

    Raises ValueError (including pydantic's ValidationError) for GenAI spans with
    unusable data; the receiver reports those as rejected.
    """
    if is_stardust_span(span):
        return None  # our own exported span coming back round: never re-ingest
    attrs = {**span.resource, **span.attributes}  # span attributes win over resource
    tokens_in = _token(attrs.get(GEN_AI_INPUT_TOKENS))
    tokens_out = _token(attrs.get(GEN_AI_OUTPUT_TOKENS))
    if tokens_in is None and tokens_out is None:
        return None

    cache_read = _token(attrs.get(GEN_AI_CACHE_READ))
    cache_write = _token(attrs.get(GEN_AI_CACHE_WRITE))
    # Current semconv counts cached tokens inside input_tokens. Older Anthropic
    # instrumentations don't; if cache reads exceed input, add the cache back in.
    if cache_read and tokens_in is not None and cache_read > tokens_in:
        tokens_in += cache_read + (cache_write or 0)

    provider = str(attrs.get(GEN_AI_PROVIDER) or attrs.get(GEN_AI_SYSTEM) or "unknown").lower()
    provider = _PROVIDER_ALIASES.get(provider, provider)
    model = str(attrs.get(GEN_AI_RESPONSE_MODEL) or attrs.get(GEN_AI_REQUEST_MODEL) or "unknown")

    layer = attrs.get(STARDUST_SOURCE_LAYER)
    source_layer = layer if layer in {m.value for m in SourceLayer} else SourceLayer.infra_agent.value

    ns = span.end_ns or span.start_ns
    timestamp = datetime.fromtimestamp(ns / 1e9, tz=timezone.utc) if ns else datetime.now(timezone.utc)

    has_context = valid_hex_id(span.trace_id, 32) and valid_hex_id(span.span_id, 16)
    return UsageEvent(
        event_id=event_id_for_span(span.trace_id, span.span_id),
        otel_trace_id=span.trace_id if has_context else None,
        otel_span_id=span.span_id if has_context else None,
        source_layer=source_layer,
        provider=provider,
        model=model,
        region=attrs.get(CLOUD_REGION),
        timestamp=timestamp,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        tokens_cached_in=cache_read,
        energy_reuse_factor=_erf(attrs.get(STARDUST_ERF)),
        user_id=_uuid_or_none(attrs.get(STARDUST_USER_ID)),
        org_id=_uuid_or_none(attrs.get(STARDUST_ORG_ID)),
    )


# --- enriched event → span attributes -------------------------------------------------


def event_attributes(e: EnrichedEvent) -> Dict[str, Any]:
    """gen_ai.* where OTel has a convention (§11.1), stardust.* for everything else (§11.2)."""
    attrs: Dict[str, Any] = {
        GEN_AI_PROVIDER: e.provider,
        GEN_AI_REQUEST_MODEL: e.model,
        GEN_AI_INPUT_TOKENS: e.tokens_in,
        GEN_AI_OUTPUT_TOKENS: e.tokens_out,
        GEN_AI_CACHE_READ: e.tokens_cached_in,
        CLOUD_REGION: e.region,
        STARDUST_EVENT_ID: str(e.event_id),
        STARDUST_SOURCE_LAYER: e.source_layer.value,
        "stardust.job.sji": e.job_sji,
        "stardust.tokens.estimated": e.tokens_estimated,
        "stardust.cost.usd": e.cost_usd,
        "stardust.impact.energy_wh": e.energy_wh,
        "stardust.impact.co2e_g": e.co2e_g,
        "stardust.impact.water_ml": e.water_ml,
        "stardust.impact.water_onsite_ml": e.water_onsite_ml,
        "stardust.impact.water_offsite_ml": e.water_offsite_ml,
        "stardust.impact.heat_rejected_wh": e.heat_rejected_wh,
        "stardust.impact.heat_recovered_wh": e.heat_recovered_wh,
        STARDUST_ERF: e.energy_reuse_factor,
        "stardust.impact.confidence_tier": e.confidence_tier.value,
        "stardust.indicator.code": e.indicator_code,
        "stardust.esc.code": e.energy_source_code,
        "stardust.grid.style": e.grid_style.value,
        "stardust.grid.majority_share_pct": e.grid_majority_share_pct,
        "stardust.grid.diversified": e.grid_diversified,
        "stardust.grid.intensity_g_per_kwh": e.grid_intensity_g_per_kwh,
        "stardust.model.tier": e.model_tier,
        "stardust.methodology.version": e.methodology_version,
    }
    for code, pct in (e.energy_mix_breakdown or {}).items():
        attrs[f"stardust.grid.mix.{code}"] = float(pct)
    return {k: v for k, v in attrs.items() if v is not None}


class ImpactSpanExporter:
    """Emits enriched events as OTel spans through Core's own (non-global) tracer provider."""

    def __init__(self, exporter: SpanExporter, version: str, batch: bool = True):
        self.provider = TracerProvider(resource=_resource(version))
        processor = BatchSpanProcessor(exporter) if batch else SimpleSpanProcessor(exporter)
        self.provider.add_span_processor(processor)
        self.tracer = self.provider.get_tracer("stardust_core", version)

    def export(self, event: EnrichedEvent) -> None:
        try:
            context = None
            if valid_hex_id(event.otel_trace_id, 32) and valid_hex_id(event.otel_span_id, 16):
                ctx = SpanContext(
                    trace_id=int(event.otel_trace_id, 16),  # type: ignore[arg-type]
                    span_id=int(event.otel_span_id, 16),  # type: ignore[arg-type]
                    is_remote=True,
                    trace_flags=TraceFlags(TraceFlags.SAMPLED),
                )
                context = trace.set_span_in_context(NonRecordingSpan(ctx))
            # Timed at the event (for OTLP input: the GenAI span's end, when usage was known).
            at = int(event.timestamp.timestamp() * 1e9)
            span = self.tracer.start_span(
                SPAN_NAME, context=context, kind=SpanKind.INTERNAL,
                attributes=event_attributes(event), start_time=at,
            )
            span.end(end_time=at)
        except Exception:  # noqa: BLE001 - exporting must never break ingestion
            log.exception("Failed to export Stardust span")

    def shutdown(self) -> None:
        self.provider.shutdown()


# --- metrics ----------------------------------------------------------------------------


def metric_attributes(e: EnrichedEvent) -> Dict[str, Any]:
    """Dimensions for dashboards. Deliberately low-cardinality: no user or event ids."""
    attrs = {
        GEN_AI_PROVIDER: e.provider,
        GEN_AI_REQUEST_MODEL: e.model,
        CLOUD_REGION: e.region or "unknown",
        STARDUST_SOURCE_LAYER: e.source_layer.value,
        "stardust.model.tier": e.model_tier,
        "stardust.impact.confidence_tier": e.confidence_tier.value,
        "stardust.esc.code": e.energy_source_code,
        "stardust.indicator.grade": e.indicator_code[0],
    }
    if e.org_id:
        attrs[STARDUST_ORG_ID] = str(e.org_id)  # team / cost-center breakdown (§6.3)
    return attrs


class ImpactMetrics:
    """Running totals as OTel counters, exported periodically by ``reader``."""

    def __init__(self, reader: MetricReader, version: str):
        self.provider = MeterProvider(resource=_resource(version), metric_readers=[reader])
        meter = self.provider.get_meter("stardust_core", version)
        self.requests = meter.create_counter("stardust.ai.requests", unit="{request}",
                                             description="Metered AI operations")
        self.tokens = meter.create_counter("stardust.ai.tokens", unit="{token}",
                                           description="Tokens, by gen_ai.token.type (input includes cached)")
        self.cached_tokens = meter.create_counter("stardust.ai.tokens.cached", unit="{token}",
                                                  description="Input tokens served from a prompt cache")
        self.cost = meter.create_counter("stardust.cost", unit="USD",
                                         description="Cost of operations with a known price")
        self.energy = meter.create_counter("stardust.energy", unit="Wh", description="Electricity")
        self.co2e = meter.create_counter("stardust.co2e", unit="g", description="Greenhouse gases, CO2e")
        self.water = meter.create_counter("stardust.water", unit="mL",
                                          description="Water, by stardust.water.scope = onsite / offsite")
        self.heat_rejected = meter.create_counter("stardust.heat.rejected", unit="Wh",
                                                  description="Heat from the electricity used")
        self.heat_recovered = meter.create_counter("stardust.heat.recovered", unit="Wh",
                                                   description="Heat reused, where the facility reports its ERF")

    def record(self, e: EnrichedEvent) -> None:
        try:
            attrs = metric_attributes(e)
            self.requests.add(1, {**attrs, "stardust.cost.known": e.cost_usd is not None})
            if e.tokens_in:
                self.tokens.add(e.tokens_in, {**attrs, "gen_ai.token.type": "input"})
            if e.tokens_out:
                self.tokens.add(e.tokens_out, {**attrs, "gen_ai.token.type": "output"})
            if e.tokens_cached_in:
                self.cached_tokens.add(e.tokens_cached_in, attrs)
            if e.cost_usd is not None:
                self.cost.add(e.cost_usd, attrs)
            self.energy.add(e.energy_wh, attrs)
            self.co2e.add(e.co2e_g, attrs)
            if e.water_onsite_ml is not None and e.water_offsite_ml is not None:
                self.water.add(e.water_onsite_ml, {**attrs, "stardust.water.scope": "onsite"})
                self.water.add(e.water_offsite_ml, {**attrs, "stardust.water.scope": "offsite"})
            else:
                self.water.add(e.water_ml, attrs)
            if e.heat_rejected_wh is not None:
                self.heat_rejected.add(e.heat_rejected_wh, attrs)
            if e.heat_recovered_wh is not None:
                self.heat_recovered.add(e.heat_recovered_wh, attrs)
        except Exception:  # noqa: BLE001
            log.exception("Failed to record Stardust metrics")

    def shutdown(self) -> None:
        self.provider.shutdown()


def _resource(version: str) -> Resource:
    return Resource.create({SERVICE_NAME_ATTR: SERVICE_NAME, "service.version": version})


# --- export configuration ------------------------------------------------------------------


class Telemetry:
    """Whatever Core exports: spans, metrics, both or neither."""

    def __init__(self, spans: Optional[ImpactSpanExporter] = None, metrics: Optional[ImpactMetrics] = None):
        self.spans, self.metrics = spans, metrics

    def record(self, event: EnrichedEvent) -> None:
        if self.spans:
            self.spans.export(event)
        if self.metrics:
            self.metrics.record(event)

    def shutdown(self) -> None:
        for part in (self.spans, self.metrics):
            if part:
                try:
                    part.shutdown()
                except Exception:  # noqa: BLE001
                    log.exception("Telemetry shutdown failed")


def otlp_base(endpoint: str) -> str:
    """Accept a base endpoint, or (for backward compatibility) a full .../v1/traces URL."""
    base = endpoint.rstrip("/")
    for suffix in ("/v1/traces", "/v1/metrics"):
        if base.endswith(suffix):
            return base[: -len(suffix)]
    return base


def build_telemetry(
    *,
    version: str,
    endpoint: Optional[str],
    protocol: str = "http/protobuf",
    headers: Optional[Dict[str, str]] = None,
    signals: Iterable[str] = ("traces", "metrics"),
    metrics_interval_s: float = 60.0,
    span_exporter: Optional[SpanExporter] = None,
    metric_reader: Optional[MetricReader] = None,
) -> Telemetry:
    """OTLP exporters for Core. ``span_exporter`` / ``metric_reader`` override the network ones (tests)."""
    signals = set(signals)
    spans = metrics = None
    if span_exporter is not None:
        spans = ImpactSpanExporter(span_exporter, version, batch=False)
    if metric_reader is not None:
        metrics = ImpactMetrics(metric_reader, version)
    if not endpoint:
        return Telemetry(spans, metrics)

    base, headers = otlp_base(endpoint), dict(headers or {})
    if protocol == "grpc":
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

        trace_kw = metric_kw = {"endpoint": base, "headers": headers, "insecure": base.startswith("http://")}
    elif protocol in ("http/protobuf", "http"):
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter  # type: ignore[no-redef]
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter  # type: ignore[no-redef]

        trace_kw = {"endpoint": f"{base}/v1/traces", "headers": headers}
        metric_kw = {"endpoint": f"{base}/v1/metrics", "headers": headers}
    else:
        raise ValueError(f"unsupported OTLP protocol {protocol!r}; use http/protobuf or grpc")

    if spans is None and "traces" in signals:
        spans = ImpactSpanExporter(OTLPSpanExporter(**trace_kw), version)
    if metrics is None and "metrics" in signals:
        reader = PeriodicExportingMetricReader(
            OTLPMetricExporter(**metric_kw), export_interval_millis=int(metrics_interval_s * 1000)
        )
        metrics = ImpactMetrics(reader, version)
    log.info("Exporting Stardust %s via OTLP/%s to %s", "+".join(sorted(signals)), protocol, base)
    return Telemetry(spans, metrics)


# --- gRPC receiver ----------------------------------------------------------------------------

# Processes decoded spans; returns (rejected count, error message).
SpanIngest = Callable[[List[OtlpSpan]], Tuple[int, str]]


class _TraceService(trace_service_pb2_grpc.TraceServiceServicer):
    def __init__(self, ingest: SpanIngest):
        self._ingest = ingest

    async def Export(self, request: ExportTraceServiceRequest, context: Any) -> ExportTraceServiceResponse:  # noqa: N802
        rejected, message = self._ingest(spans_from_request(request))
        response = ExportTraceServiceResponse()
        if rejected:
            response.partial_success.rejected_spans = rejected
            response.partial_success.error_message = message
        return response


async def start_grpc_receiver(listen: str, ingest: SpanIngest) -> Tuple[Any, int]:
    """Serve OTLP/gRPC TraceService on ``listen`` (e.g. "0.0.0.0:4317"). Returns (server, bound port)."""
    server = grpc.aio.server(options=[("grpc.max_receive_message_length", MAX_BODY_BYTES)])
    trace_service_pb2_grpc.add_TraceServiceServicer_to_server(_TraceService(ingest), server)
    port = server.add_insecure_port(listen)
    if not port:
        raise RuntimeError(f"could not bind the OTLP/gRPC receiver to {listen}")
    await server.start()
    log.info("OTLP/gRPC receiver listening on %s (port %d)", listen, port)
    return server, port
