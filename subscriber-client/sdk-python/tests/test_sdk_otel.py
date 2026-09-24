import asyncio
import uuid

import anthropic
import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode

from conftest import FakeCore
from stardust_sdk import Stardust, instrument
from test_instrument import AHTTP, ANTHROPIC_MESSAGE, ANTHROPIC_STREAM, ARGS, anthropic_client

# Same namespace Core uses (stardust_core.otel.EVENT_ID_NAMESPACE).
CORE_NAMESPACE = uuid.UUID("5a2b7f0e-3c1d-4e8a-9b6f-2d4c8e1a7f30")


@pytest.fixture
def spans():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider, exporter


def ids(span):
    return format(span.context.trace_id, "032x"), format(span.context.span_id, "016x")


def test_genai_span_per_call_and_linked_direct_event(spans):
    provider, exporter = spans
    core = FakeCore()
    sd = Stardust("http://core.test", region="us-east-1", transport=core, otel=provider,
                  org_id="0b5f6f0e-8a52-4a8e-9f0b-7c1d2e3f4a5b")
    client = instrument(anthropic_client(), sd)
    client.messages.create(**ARGS)
    assert sd.flush(2)

    [span] = exporter.get_finished_spans()
    assert span.name == "chat claude-sonnet-5"
    assert span.kind == SpanKind.CLIENT
    assert span.end_time >= span.start_time
    a = span.attributes
    assert a["gen_ai.operation.name"] == "chat"
    assert a["gen_ai.provider.name"] == "anthropic"
    assert a["gen_ai.request.model"] == a["gen_ai.response.model"] == "claude-sonnet-5"
    assert (a["gen_ai.usage.input_tokens"], a["gen_ai.usage.output_tokens"], a["gen_ai.usage.cache_read.input_tokens"]) == (400, 20, 300)
    assert (a["cloud.region"], a["stardust.source_layer"]) == ("us-east-1", "infra_agent")
    assert a["stardust.org_id"] == "0b5f6f0e-8a52-4a8e-9f0b-7c1d2e3f4a5b"
    assert "stardust.event_id" not in a  # Core would treat it as its own span and ignore it

    # The direct event carries the span's ids and the id Core derives from them.
    [event] = core.events
    trace_id, span_id = ids(span)
    assert (event["otel_trace_id"], event["otel_span_id"]) == (trace_id, span_id)
    assert event["event_id"] == str(uuid.uuid5(CORE_NAMESPACE, f"{trace_id}:{span_id}"))
    sd.close(1)


def test_span_covers_the_whole_stream(spans):
    provider, exporter = spans
    sd = Stardust(None, otel=provider)  # OTel only: nothing sent directly
    client = instrument(anthropic_client(), sd)
    with client.messages.create(**ARGS, stream=True) as stream:
        it = iter(stream)
        next(it)
        assert exporter.get_finished_spans() == ()  # still streaming: span open
        for _ in it:
            pass
    [span] = exporter.get_finished_spans()
    assert span.attributes["gen_ai.usage.output_tokens"] == 42


def test_api_error_marks_span_and_records_nothing(spans):
    provider, exporter = spans
    core = FakeCore()
    sd = Stardust("http://core.test", transport=core, otel=provider)

    def failing(request):
        return AHTTP.Response(500, json={"type": "error", "error": {"type": "api_error", "message": "down"}})

    client = instrument(anthropic.Anthropic(api_key="test", max_retries=0,
                                            http_client=AHTTP.Client(transport=AHTTP.MockTransport(failing))), sd)
    with pytest.raises(anthropic.InternalServerError):
        client.messages.create(**ARGS)
    assert sd.flush(2)
    [span] = exporter.get_finished_spans()
    assert span.status.status_code == StatusCode.ERROR
    assert span.attributes["error.type"] == "InternalServerError"
    assert any(e.name == "exception" for e in span.events)
    assert core.events == []
    sd.close(1)


def test_async_calls_get_spans(spans):
    provider, exporter = spans
    sd = Stardust(None, otel=provider)
    client = instrument(anthropic_client(async_=True), sd)

    async def go():
        await client.messages.create(**ARGS)
        stream = await client.messages.create(**ARGS, stream=True)
        async for _ in stream:
            pass

    asyncio.run(go())
    assert [s.attributes["gen_ai.usage.output_tokens"] for s in exporter.get_finished_spans()] == [20, 42]


def test_manual_record_emits_span_with_semconv_provider(spans):
    provider, exporter = spans
    sd = Stardust(None, otel=provider)
    sd.record("google", "gemini-2.5-flash", 100, 10)
    [span] = exporter.get_finished_spans()
    assert span.name == "generate_content gemini-2.5-flash"
    assert span.attributes["gen_ai.provider.name"] == "gcp.gemini"


def test_spans_are_children_of_the_active_span(spans):
    provider, exporter = spans
    sd = Stardust(None, otel=provider)
    client = instrument(anthropic_client(), sd)
    with provider.get_tracer("app").start_as_current_span("handle request") as parent:
        client.messages.create(**ARGS)
    chat = next(s for s in exporter.get_finished_spans() if s.name.startswith("chat"))
    assert chat.parent.span_id == parent.get_span_context().span_id


def test_otel_needs_a_destination():
    with pytest.raises(ValueError):
        Stardust(None)


def test_disabled_emits_no_spans(spans):
    provider, exporter = spans
    sd = Stardust(None, otel=provider, enabled=False)
    instrument(anthropic_client(), sd).messages.create(**ARGS)
    assert exporter.get_finished_spans() == ()
