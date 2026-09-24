"""OpenTelemetry harmonization (spec §11).

Receiver: OTLP/HTTP trace payloads (protobuf or JSON). Spans carrying GenAI usage
(``gen_ai.usage.*``) become Stardust usage events, keyed deterministically by
trace/span id so collector retries don't double count.

Exporter: each enriched event is emitted as a ``stardust.impact`` span with the
usage side under ``gen_ai.*`` (§11.1) and everything environmental under the
``stardust.*`` namespace (§11.2). When the event came in over OTLP, that span is a
child of the original GenAI span, so it sits in the same trace in any OTel backend.

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
from typing import Any, Dict, Iterable, List, Optional, Tuple

from opentelemetry import trace
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
    ExportTraceServiceResponse,
)
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

    return UsageEvent(
        event_id=uuid.uuid5(EVENT_ID_NAMESPACE, f"{span.trace_id}:{span.span_id}"),
        source_layer=source_layer,
        provider=provider,
        model=model,
        region=attrs.get(CLOUD_REGION),
        timestamp=timestamp,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        tokens_cached_in=cache_read,
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


@dataclass(frozen=True)
class Parent:
    trace_id: str
    span_id: str
    start_ns: int
    end_ns: int


class ImpactSpanExporter:
    """Emits enriched events as OTel spans through Core's own (non-global) tracer provider."""

    def __init__(self, exporter: SpanExporter, version: str, batch: bool = True):
        self.provider = TracerProvider(resource=Resource.create({
            SERVICE_NAME_ATTR: SERVICE_NAME,
            "service.version": version,
        }))
        processor = BatchSpanProcessor(exporter) if batch else SimpleSpanProcessor(exporter)
        self.provider.add_span_processor(processor)
        self.tracer = self.provider.get_tracer("stardust_core", version)

    def export(self, event: EnrichedEvent, parent: Optional[Parent] = None) -> None:
        try:
            context = None
            if parent:
                ctx = SpanContext(
                    trace_id=int(parent.trace_id, 16),
                    span_id=int(parent.span_id, 16),
                    is_remote=True,
                    trace_flags=TraceFlags(TraceFlags.SAMPLED),
                )
                context = trace.set_span_in_context(NonRecordingSpan(ctx))
                # Sit inside the parent's time range, at its end (when usage is known).
                start = end = parent.end_ns or parent.start_ns
            else:
                start = end = int(event.timestamp.timestamp() * 1e9)
            span = self.tracer.start_span(
                SPAN_NAME, context=context, kind=SpanKind.INTERNAL,
                attributes=event_attributes(event), start_time=start,
            )
            span.end(end_time=end)
        except Exception:  # noqa: BLE001 - exporting must never break ingestion
            log.exception("Failed to export Stardust span")

    def shutdown(self) -> None:
        self.provider.shutdown()
