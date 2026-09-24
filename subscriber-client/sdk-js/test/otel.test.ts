import assert from "node:assert/strict";
import { test } from "node:test";

import Anthropic from "@anthropic-ai/sdk";
import { BasicTracerProvider, InMemorySpanExporter, SimpleSpanProcessor } from "@opentelemetry/sdk-trace-base";

import { Stardust } from "../src/client.ts";
import { instrument } from "../src/instrument.ts";
import { eventIdForSpan } from "../src/otel.ts";
import { fakeCore, sse, vendorFetch } from "./helpers.ts";

const MESSAGE = {
  id: "msg_1", type: "message", role: "assistant", model: "claude-sonnet-5",
  content: [{ type: "text", text: "hi" }], stop_reason: "end_turn", stop_sequence: null,
  usage: { input_tokens: 100, output_tokens: 20, cache_read_input_tokens: 300 },
};
const STREAM = sse([
  { type: "message_start", message: { ...MESSAGE, content: [], stop_reason: null, usage: { input_tokens: 100, output_tokens: 1, cache_read_input_tokens: 300 } } },
  { type: "message_delta", delta: { stop_reason: "end_turn", stop_sequence: null }, usage: { output_tokens: 42 } },
  { type: "message_stop" },
]);
const ARGS = { model: "claude-sonnet-5", max_tokens: 10, messages: [{ role: "user" as const, content: "hi" }] };

function tracing() {
  const exporter = new InMemorySpanExporter();
  const provider = new BasicTracerProvider({ spanProcessors: [new SimpleSpanProcessor(exporter)] });
  return { exporter, tracer: provider.getTracer("test") };
}

test("event ids match Core's derivation (same as Python uuid5)", () => {
  assert.equal(eventIdForSpan("5b8efff798038103d269b633813fc60c", "eee19b7ec3c1b174"), "e247909e-4303-59e1-b14e-f0590e1fcf7c");
});

test("GenAI span per call, linked to the direct event", async () => {
  const { exporter, tracer } = tracing();
  const core = fakeCore();
  const sd = new Stardust({ apiBase: "http://core.test", fetch: core.fetch, tracer, region: "us-east-1", orgId: "0b5f6f0e-8a52-4a8e-9f0b-7c1d2e3f4a5b" });
  const client = instrument(new Anthropic({ apiKey: "test", fetch: vendorFetch(MESSAGE, STREAM), maxRetries: 0 }), sd);
  await client.messages.create(ARGS);
  await sd.flush(2000);

  const [span] = exporter.getFinishedSpans();
  assert.equal(span.name, "chat claude-sonnet-5");
  assert.equal(span.kind, 2); // CLIENT
  const a = span.attributes;
  assert.deepEqual(
    [a["gen_ai.operation.name"], a["gen_ai.provider.name"], a["gen_ai.request.model"], a["gen_ai.response.model"]],
    ["chat", "anthropic", "claude-sonnet-5", "claude-sonnet-5"],
  );
  assert.deepEqual([a["gen_ai.usage.input_tokens"], a["gen_ai.usage.output_tokens"], a["gen_ai.usage.cache_read.input_tokens"]], [400, 20, 300]);
  assert.deepEqual([a["cloud.region"], a["stardust.source_layer"], a["stardust.org_id"]], ["us-east-1", "infra_agent", "0b5f6f0e-8a52-4a8e-9f0b-7c1d2e3f4a5b"]);
  assert.equal("stardust.event_id" in a, false);

  const [event] = core.events;
  const { traceId, spanId } = span.spanContext();
  assert.deepEqual([event.otel_trace_id, event.otel_span_id], [traceId, spanId]);
  assert.equal(event.event_id, eventIdForSpan(traceId, spanId));
});

test("span stays open until the stream ends; tracer-only mode sends nothing directly", async () => {
  const { exporter, tracer } = tracing();
  const sd = new Stardust({ apiBase: null, tracer });
  const client = instrument(new Anthropic({ apiKey: "test", fetch: vendorFetch(MESSAGE, STREAM), maxRetries: 0 }), sd);
  const stream = await client.messages.create({ ...ARGS, stream: true });
  const it = stream[Symbol.asyncIterator]();
  await it.next();
  assert.equal(exporter.getFinishedSpans().length, 0);
  while (!(await it.next()).done) { /* drain */ }
  const [span] = exporter.getFinishedSpans();
  assert.equal(span.attributes["gen_ai.usage.output_tokens"], 42);
  assert.equal(sd.pending, 0);
});

test("API errors mark the span and record no usage", async () => {
  const { exporter, tracer } = tracing();
  const core = fakeCore();
  const sd = new Stardust({ apiBase: "http://core.test", fetch: core.fetch, tracer });
  const failing = (async () => new Response(JSON.stringify({ type: "error", error: { type: "api_error", message: "down" } }), {
    status: 500, headers: { "content-type": "application/json" },
  })) as unknown as typeof fetch;
  const client = instrument(new Anthropic({ apiKey: "test", fetch: failing, maxRetries: 0 }), sd);
  await assert.rejects(client.messages.create(ARGS), Anthropic.InternalServerError);
  await sd.flush(1000);
  const [span] = exporter.getFinishedSpans();
  assert.equal(span.status.code, 2); // ERROR
  assert.equal(span.attributes["error.type"], "InternalServerError");
  assert.ok(span.events.some((e) => e.name === "exception"));
  assert.equal(core.events.length, 0);
});

test("manual record emits a span with the semconv provider name", () => {
  const { exporter, tracer } = tracing();
  const sd = new Stardust({ apiBase: null, tracer });
  sd.record({ provider: "google", model: "gemini-2.5-flash", tokensIn: 100, tokensOut: 10 });
  const [span] = exporter.getFinishedSpans();
  assert.equal(span.name, "generate_content gemini-2.5-flash");
  assert.equal(span.attributes["gen_ai.provider.name"], "gcp.gemini");
});

test("needs apiBase or tracer; disabled emits no spans", () => {
  assert.throws(() => new Stardust({ apiBase: null }));
  const { exporter, tracer } = tracing();
  new Stardust({ apiBase: null, tracer, enabled: false }).record({ provider: "openai", model: "gpt-4o", tokensIn: 1, tokensOut: 1 });
  assert.equal(exporter.getFinishedSpans().length, 0);
});
