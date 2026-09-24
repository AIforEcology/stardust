import uuid

import grpc
import pytest
from fastapi.testclient import TestClient
from opentelemetry.proto.collector.trace.v1 import trace_service_pb2_grpc
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from stardust_core.api import create_app
from stardust_core.config import DEFAULT_ESC, DEFAULT_METHODOLOGY, Settings, parse_protocol, parse_signals
from stardust_core.otel import build_telemetry, event_id_for_span, otlp_base
from test_otel import END_NS, SPAN, TRACE, genai_attrs, protobuf_payload


def settings(pricing_file, **kw):
    return Settings(DEFAULT_METHODOLOGY, DEFAULT_ESC, pricing_file, "https://example.org/donate", None, **kw)


# --- gRPC receiver -------------------------------------------------------------------


@pytest.fixture
def grpc_core(pricing_file):
    exported = InMemorySpanExporter()
    app = create_app(settings(pricing_file, otlp_grpc_listen="127.0.0.1:0"), span_exporter=exported)
    with TestClient(app) as client:
        channel = grpc.insecure_channel(f"127.0.0.1:{app.state.otlp_grpc_port}")
        yield client, trace_service_pb2_grpc.TraceServiceStub(channel), exported
        channel.close()


def grpc_request(spans):
    req = ExportTraceServiceRequest()
    req.ParseFromString(protobuf_payload(spans))
    return req


def test_grpc_receiver_ingests_genai_spans(grpc_core):
    client, stub, exported = grpc_core
    resp = stub.Export(grpc_request([(TRACE, SPAN, "chat", genai_attrs()), (TRACE, "cccccccccccccccc", "db", {"db.system": "pg"})]), timeout=5)
    assert resp.partial_success.rejected_spans == 0
    [e] = client.get("/v1/events").json()
    assert e["event_id"] == str(event_id_for_span(TRACE, SPAN))
    assert (e["otel_trace_id"], e["otel_span_id"], e["indicator_code"]) == (TRACE, SPAN, "C3-S")
    [span] = exported.get_finished_spans()
    assert format(span.parent.span_id, "016x") == SPAN


def test_grpc_partial_success_and_dedupe_with_http(grpc_core):
    client, stub, _ = grpc_core
    spans = [(TRACE, SPAN, "good", genai_attrs()),
             (TRACE, "dddddddddddddddd", "bad", genai_attrs(**{"gen_ai.usage.output_tokens": -1}))]
    resp = stub.Export(grpc_request(spans), timeout=5)
    assert resp.partial_success.rejected_spans == 1
    assert "bad" in resp.partial_success.error_message
    # The same span arriving over HTTP too (e.g. two collector pipelines) is counted once.
    client.post("/v1/traces", content=protobuf_payload(spans[:1]), headers={"content-type": "application/x-protobuf"})
    assert client.get("/v1/summary").json()["events"] == 1


def test_grpc_receiver_off_by_default(pricing_file):
    app = create_app(settings(pricing_file))
    with TestClient(app) as client:
        assert app.state.otlp_grpc_port is None
        assert client.get("/healthz").json()["otlp_grpc_receiver"] is None


# --- trace context on direct events --------------------------------------------------------


def test_direct_event_with_trace_context_exports_as_child(pricing_file):
    exported = InMemorySpanExporter()
    with TestClient(create_app(settings(pricing_file), span_exporter=exported)) as client:
        r = client.post("/v1/events", json={
            "event_id": str(event_id_for_span(TRACE, SPAN)), "otel_trace_id": TRACE, "otel_span_id": SPAN,
            "source_layer": "infra_agent", "provider": "anthropic", "model": "claude-test-opus",
            "timestamp": "2026-09-23T12:00:00Z", "tokens_in": 10, "tokens_out": 10,
        })
        assert r.status_code == 200
        [span] = exported.get_finished_spans()
        assert format(span.context.trace_id, "032x") == TRACE
        assert format(span.parent.span_id, "016x") == SPAN
        # Malformed trace context is rejected, not silently dropped.
        bad = client.post("/v1/events", json={**r.json(), "event_id": str(uuid.uuid4()), "otel_trace_id": "XYZ"})
        assert bad.status_code == 422


# --- metrics ----------------------------------------------------------------------------------


def metric_points(reader):
    """{metric name: [(attributes, value), ...]} from an InMemoryMetricReader."""
    out = {}
    data = reader.get_metrics_data()
    for rm in data.resource_metrics:
        assert rm.resource.attributes["service.name"] == "stardust-core"
        for sm in rm.scope_metrics:
            for m in sm.metrics:
                out[m.name] = [(dict(p.attributes), p.value) for p in m.data.data_points]
    return out


def test_metrics_accumulate_by_dimension(pricing_file):
    reader = InMemoryMetricReader()
    with TestClient(create_app(settings(pricing_file), metric_reader=reader)) as client:
        base = {"source_layer": "infra_agent", "provider": "anthropic", "model": "claude-test-opus",
                "region": "us-east-1", "timestamp": "2026-09-23T12:00:00Z"}
        org = str(uuid.uuid4())
        client.post("/v1/events", json={**base, "tokens_in": 500, "tokens_out": 500, "tokens_cached_in": 200, "org_id": org})
        client.post("/v1/events", json={**base, "tokens_in": 500, "tokens_out": 500, "org_id": org})
        client.post("/v1/events", json={**base, "provider": "openai", "model": "unknown", "region": "eu-north-1",
                                        "tokens_in": 100, "tokens_out": 10, "tokens_estimated": True})
        m = metric_points(reader)

    requests = {(a["gen_ai.provider.name"], a["stardust.cost.known"]): v for a, v in m["stardust.ai.requests"]}
    assert requests == {("anthropic", True): 2, ("openai", False): 1}

    tokens = {(a["gen_ai.provider.name"], a["gen_ai.token.type"]): v for a, v in m["stardust.ai.tokens"]}
    assert tokens[("anthropic", "input")] == 1000 and tokens[("anthropic", "output")] == 1000
    assert [v for _, v in m["stardust.ai.tokens.cached"]] == [200]

    # Unknown cost adds nothing to the cost total.
    cost = {a["gen_ai.provider.name"]: v for a, v in m["stardust.cost"]}
    assert set(cost) == {"anthropic"}
    assert cost["anthropic"] == pytest.approx(2 * (500 * 1e-05 + 500 * 5e-05) - 200 * (1e-05 - 1e-06))

    co2e = {a["cloud.region"]: v for a, v in m["stardust.co2e"]}
    assert co2e["us-east-1"] == pytest.approx(2 * 0.425 / 1000 * 367)
    assert co2e["eu-north-1"] < co2e["us-east-1"]

    anth = next(a for a, _ in m["stardust.energy"] if a["gen_ai.provider.name"] == "anthropic")
    assert anth["stardust.org_id"] == org
    assert anth["stardust.indicator.grade"] == "C"
    assert anth["stardust.esc.code"] == "NGP"
    assert "stardust.user_id" not in anth and "stardust.event_id" not in anth  # keep cardinality bounded

    # Water splits by scope and still sums to the total; heat recovered only where an ERF was reported.
    water = {}
    for a, v in m["stardust.water"]:
        water[a["stardust.water.scope"]] = water.get(a["stardust.water.scope"], 0) + v
    anth_wh = 2 * 0.425
    assert water["onsite"] > 0 and water["offsite"] > 0
    assert sum(v for a, v in m["stardust.heat.rejected"] if a["gen_ai.provider.name"] == "anthropic") == pytest.approx(anth_wh)
    assert "stardust.heat.recovered" not in m


def test_duplicate_events_do_not_inflate_metrics(pricing_file):
    reader = InMemoryMetricReader()
    with TestClient(create_app(settings(pricing_file), metric_reader=reader)) as client:
        event = {"event_id": str(uuid.uuid4()), "source_layer": "infra_agent", "provider": "anthropic",
                 "model": "claude-test-opus", "timestamp": "2026-09-23T12:00:00Z", "tokens_in": 1, "tokens_out": 1}
        client.post("/v1/events", json=event)
        client.post("/v1/events", json=event)
        assert [v for _, v in metric_points(reader)["stardust.ai.requests"]] == [1]


# --- configuration --------------------------------------------------------------------------------


def test_protocol_signal_and_endpoint_parsing():
    assert parse_protocol("http") == parse_protocol("HTTP/protobuf") == "http/protobuf"
    assert parse_protocol("grpc") == "grpc"
    with pytest.raises(ValueError):
        parse_protocol("http/json")
    assert parse_signals("traces, METRICS") == ("traces", "metrics")
    assert parse_signals("metrics") == ("metrics",)
    with pytest.raises(ValueError):
        parse_signals("logs")
    assert otlp_base("http://c:4318") == "http://c:4318"
    assert otlp_base("http://c:4318/v1/traces") == "http://c:4318"  # PR #4's full-URL form still works
    assert otlp_base("http://c:4318/v1/metrics/") == "http://c:4318"


@pytest.mark.parametrize("protocol,trace_module,metric_endpoint", [
    ("http/protobuf", "opentelemetry.exporter.otlp.proto.http.trace_exporter", "http://c:4318/v1/metrics"),
    ("grpc", "opentelemetry.exporter.otlp.proto.grpc.trace_exporter", None),
])
def test_build_telemetry_picks_exporters(protocol, trace_module, metric_endpoint):
    endpoint = "http://c:4318" if protocol != "grpc" else "http://c:4317"
    t = build_telemetry(version="0.1.0", endpoint=endpoint, protocol=protocol, metrics_interval_s=3600)
    try:
        processor = t.spans.provider._active_span_processor._span_processors[0]
        assert type(processor.span_exporter).__module__ == trace_module
        assert t.metrics is not None
    finally:
        t.shutdown()


def test_build_telemetry_signals_and_off():
    t = build_telemetry(version="0.1.0", endpoint="http://c:4318", signals=("metrics",), metrics_interval_s=3600)
    assert t.spans is None and t.metrics is not None
    t.shutdown()
    off = build_telemetry(version="0.1.0", endpoint=None)
    assert off.spans is None and off.metrics is None
    with pytest.raises(ValueError):
        build_telemetry(version="0.1.0", endpoint="http://c:4318", protocol="carrier-pigeon")
