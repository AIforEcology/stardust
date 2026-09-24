import gzip
import json
import uuid

import pytest
from fastapi.testclient import TestClient
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
    ExportTraceServiceResponse,
)
from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from stardust_core.api import create_app
from stardust_core.config import DEFAULT_ESC, DEFAULT_METHODOLOGY, Settings, parse_headers
from stardust_core.otel import EVENT_ID_NAMESPACE, OtlpSpan, span_to_event

TRACE = "5b8efff798038103d269b633813fc60c"
SPAN = "eee19b7ec3c1b174"
END_NS = 1_790_000_000_000_000_000  # 2026-09-21


def genai_attrs(**over):
    attrs = {
        "gen_ai.provider.name": "anthropic",
        "gen_ai.request.model": "claude-test-opus",
        "gen_ai.response.model": "claude-test-opus",
        "gen_ai.usage.input_tokens": 500,
        "gen_ai.usage.output_tokens": 500,
    }
    attrs.update(over)
    return {k: v for k, v in attrs.items() if v is not None}


# --- payload builders --------------------------------------------------------------


def _pb_value(v):
    if isinstance(v, bool):
        return AnyValue(bool_value=v)
    if isinstance(v, int):
        return AnyValue(int_value=v)
    if isinstance(v, float):
        return AnyValue(double_value=v)
    return AnyValue(string_value=str(v))


def protobuf_payload(spans, resource=None):
    req = ExportTraceServiceRequest()
    rs = req.resource_spans.add()
    for k, v in (resource or {"service.name": "my-llm-app", "cloud.region": "us-east-1"}).items():
        rs.resource.attributes.append(KeyValue(key=k, value=_pb_value(v)))
    ss = rs.scope_spans.add()
    for trace_id, span_id, name, attrs in spans:
        s = ss.spans.add()
        s.trace_id, s.span_id, s.name = bytes.fromhex(trace_id), bytes.fromhex(span_id), name
        s.start_time_unix_nano, s.end_time_unix_nano = END_NS - 2_000_000_000, END_NS
        for k, v in attrs.items():
            s.attributes.append(KeyValue(key=k, value=_pb_value(v)))
    return req.SerializeToString()


def _json_value(v):
    if isinstance(v, bool):
        return {"boolValue": v}
    if isinstance(v, int):
        return {"intValue": str(v)}  # int64 as string, per OTLP JSON
    if isinstance(v, float):
        return {"doubleValue": v}
    return {"stringValue": str(v)}


def json_payload(spans, resource=None):
    kv = lambda d: [{"key": k, "value": _json_value(v)} for k, v in d.items()]  # noqa: E731
    return json.dumps({"resourceSpans": [{
        "resource": {"attributes": kv(resource or {"service.name": "my-llm-app", "cloud.region": "eu-north-1"})},
        "scopeSpans": [{"spans": [{
            "traceId": t, "spanId": s, "name": n,
            "startTimeUnixNano": str(END_NS - 1), "endTimeUnixNano": str(END_NS),
            "attributes": kv(a),
        } for t, s, n, a in spans]}],
    }]}).encode()


# --- fixtures ----------------------------------------------------------------------


@pytest.fixture
def exported():
    return InMemorySpanExporter()


@pytest.fixture
def client(pricing_file, exported):
    settings = Settings(DEFAULT_METHODOLOGY, DEFAULT_ESC, pricing_file, "https://example.org/donate", None)
    with TestClient(create_app(settings, span_exporter=exported)) as c:
        yield c


PB = {"content-type": "application/x-protobuf"}
JS = {"content-type": "application/json"}


# --- receiver ------------------------------------------------------------------------


def test_protobuf_genai_span_becomes_event(client):
    r = client.post("/v1/traces", content=protobuf_payload([(TRACE, SPAN, "chat claude-test-opus", genai_attrs())]), headers=PB)
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/x-protobuf"
    resp = ExportTraceServiceResponse()
    resp.ParseFromString(r.content)
    assert resp.partial_success.rejected_spans == 0

    [e] = client.get("/v1/events").json()
    assert e["event_id"] == str(uuid.uuid5(EVENT_ID_NAMESPACE, f"{TRACE}:{SPAN}"))
    assert (e["provider"], e["model"], e["region"], e["tokens_in"], e["tokens_out"]) == ("anthropic", "claude-test-opus", "us-east-1", 500, 500)
    assert e["source_layer"] == "infra_agent"
    assert e["timestamp"].startswith("2026-09-21")
    assert e["indicator_code"] == "C3-S"  # same as the direct-API worked example


def test_json_and_gzip(client):
    body = json_payload([(TRACE, SPAN, "chat", genai_attrs(**{"gen_ai.provider.name": "gcp.gemini", "gen_ai.response.model": "gemini-2.5-flash"}))])
    r = client.post("/v1/traces", content=gzip.compress(body), headers={**JS, "content-encoding": "gzip"})
    assert r.status_code == 200
    assert r.json() == {}
    [e] = client.get("/v1/events").json()
    assert (e["provider"], e["model"], e["region"], e["energy_source_code"]) == ("google", "gemini-2.5-flash", "eu-north-1", "HYD")


def test_non_genai_spans_are_ignored(client):
    spans = [
        (TRACE, SPAN, "GET /health", {"http.method": "GET"}),
        (TRACE, "aaaaaaaaaaaaaaaa", "chat", genai_attrs()),
    ]
    assert client.post("/v1/traces", content=protobuf_payload(spans), headers=PB).status_code == 200
    assert len(client.get("/v1/events").json()) == 1


def test_collector_retry_is_not_double_counted(client, exported):
    body = protobuf_payload([(TRACE, SPAN, "chat", genai_attrs())])
    client.post("/v1/traces", content=body, headers=PB)
    client.post("/v1/traces", content=body, headers=PB)
    assert client.get("/v1/summary").json()["events"] == 1
    assert len(exported.get_finished_spans()) == 1


def test_bad_spans_are_reported_as_partial_success(client):
    spans = [
        (TRACE, SPAN, "good", genai_attrs()),
        (TRACE, "bbbbbbbbbbbbbbbb", "bad", genai_attrs(**{"gen_ai.usage.input_tokens": -5})),
    ]
    r = client.post("/v1/traces", content=json_payload(spans), headers=JS)
    assert r.status_code == 200
    assert r.json()["partialSuccess"]["rejectedSpans"] == "1"
    assert "bad" in r.json()["partialSuccess"]["errorMessage"]
    assert len(client.get("/v1/events").json()) == 1


def test_invalid_payloads(client):
    assert client.post("/v1/traces", content=b"\xff\x00garbage", headers=PB).status_code == 400
    assert client.post("/v1/traces", content=b"{not json", headers=JS).status_code == 400
    assert client.post("/v1/traces", content=b"not gzip", headers={**PB, "content-encoding": "gzip"}).status_code == 400


def test_mapping_details():
    base = dict(trace_id=TRACE, span_id=SPAN, name="chat", start_ns=0, end_ns=END_NS)
    # Older semconv: gen_ai.system; request model when no response model; resource attrs; span wins.
    e = span_to_event(OtlpSpan(**base, resource={"cloud.region": "us-west-2", "gen_ai.system": "openai"},
                               attributes={"gen_ai.request.model": "gpt-4o", "gen_ai.usage.input_tokens": 10,
                                           "cloud.region": "eu-west-3", "stardust.user_id": "not-a-uuid"}))
    assert (e.provider, e.model, e.region, e.tokens_out, e.user_id) == ("openai", "gpt-4o", "eu-west-3", None, None)

    # Cache reads larger than input: an instrumentation that excludes cache from input_tokens.
    e = span_to_event(OtlpSpan(**base, attributes=genai_attrs(**{"gen_ai.usage.input_tokens": 100, "gen_ai.usage.cache_read.input_tokens": 300})))
    assert (e.tokens_in, e.tokens_cached_in) == (400, 300)
    # Cache within input: current semconv, input already includes it.
    e = span_to_event(OtlpSpan(**base, attributes=genai_attrs(**{"gen_ai.usage.input_tokens": 1000, "gen_ai.usage.cache_read.input_tokens": 300})))
    assert (e.tokens_in, e.tokens_cached_in) == (1000, 300)

    uid = str(uuid.uuid4())
    e = span_to_event(OtlpSpan(**base, attributes=genai_attrs(**{"stardust.user_id": uid, "stardust.source_layer": "chat_plugin"})))
    assert (str(e.user_id), e.source_layer.value) == (uid, "chat_plugin")

    with pytest.raises(ValueError):
        span_to_event(OtlpSpan(**base, attributes=genai_attrs(**{"gen_ai.usage.output_tokens": True})))


# --- exporter --------------------------------------------------------------------------


def test_exported_span_is_child_of_the_genai_span(client, exported):
    client.post("/v1/traces", content=protobuf_payload([(TRACE, SPAN, "chat", genai_attrs())]), headers=PB)
    [span] = exported.get_finished_spans()
    assert span.name == "stardust.impact"
    assert format(span.context.trace_id, "032x") == TRACE
    assert format(span.parent.span_id, "016x") == SPAN
    assert span.start_time == span.end_time == END_NS
    assert span.resource.attributes["service.name"] == "stardust-core"

    a = span.attributes
    assert a["gen_ai.provider.name"] == "anthropic"
    assert a["gen_ai.usage.input_tokens"] == 500
    assert a["cloud.region"] == "us-east-1"
    assert a["stardust.indicator.code"] == "C3-S"
    assert a["stardust.esc.code"] == "NGP"
    assert a["stardust.grid.mix.NGP"] == 43.0
    assert a["stardust.impact.confidence_tier"] == "modeled"
    assert a["stardust.job.sji"].split("-")[1] == "NGP"
    assert a["stardust.impact.co2e_g"] == pytest.approx(0.425 / 1000 * 367)
    assert "stardust.cost.usd" in a


def test_direct_events_export_as_root_spans(client, exported):
    client.post("/v1/events", json={
        "source_layer": "browser_ext", "provider": "openai", "model": "unknown",
        "timestamp": "2026-09-23T12:00:00Z", "tokens_in": 100, "tokens_out": 50, "tokens_estimated": True,
    })
    [span] = exported.get_finished_spans()
    assert span.parent is None
    assert span.attributes["stardust.tokens.estimated"] is True
    assert "stardust.cost.usd" not in span.attributes  # unknown cost: omitted, not zero


def test_own_spans_are_never_reingested(client, exported):
    # Core exports into a collector that also feeds Core's receiver: the loop must stop here.
    client.post("/v1/traces", content=protobuf_payload([(TRACE, SPAN, "chat", genai_attrs())]), headers=PB)
    [ours] = exported.get_finished_spans()
    echo = protobuf_payload(
        [(TRACE, format(ours.context.span_id, "016x"), ours.name, dict(ours.attributes))],
        resource={"service.name": "stardust-core"},
    )
    client.post("/v1/traces", content=echo, headers=PB)
    assert client.get("/v1/summary").json()["events"] == 1
    assert len(exported.get_finished_spans()) == 1


def test_parse_headers():
    assert parse_headers("") == ()
    assert parse_headers("x-api-key=abc, dd-site = eu=1 ,junk") == (("x-api-key", "abc"), ("dd-site", "eu=1"))


def test_event_dedupe_on_direct_api(client):
    event = {"event_id": str(uuid.uuid4()), "source_layer": "infra_agent", "provider": "openai", "model": "gpt-4o",
             "timestamp": "2026-09-23T12:00:00Z", "tokens_in": 1, "tokens_out": 1}
    first = client.post("/v1/events", json=event).json()
    second = client.post("/v1/events", json=event).json()
    assert first["job_sji"] == second["job_sji"]  # same enriched event returned
    assert client.get("/v1/summary").json()["events"] == 1
