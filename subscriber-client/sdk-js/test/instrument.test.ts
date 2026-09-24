import assert from "node:assert/strict";
import { test } from "node:test";

import Anthropic from "@anthropic-ai/sdk";
import OpenAI from "openai";

import { instrument } from "../src/instrument.ts";
import { fakeCore, sse, stardustWith, vendorFetch } from "./helpers.ts";

const MESSAGE = {
  id: "msg_1", type: "message", role: "assistant", model: "claude-sonnet-5",
  content: [{ type: "text", text: "hi" }], stop_reason: "end_turn", stop_sequence: null,
  usage: { input_tokens: 100, output_tokens: 20, cache_read_input_tokens: 300, cache_creation_input_tokens: 0 },
};
const STREAM = sse([
  { type: "message_start", message: { ...MESSAGE, content: [], stop_reason: null, usage: { input_tokens: 100, output_tokens: 1, cache_read_input_tokens: 300 } } },
  { type: "content_block_start", index: 0, content_block: { type: "text", text: "" } },
  { type: "content_block_delta", index: 0, delta: { type: "text_delta", text: "Hello" } },
  { type: "content_block_stop", index: 0 },
  { type: "message_delta", delta: { stop_reason: "end_turn", stop_sequence: null }, usage: { output_tokens: 42 } },
  { type: "message_stop" },
]);
const ARGS = { model: "claude-sonnet-5", max_tokens: 10, messages: [{ role: "user" as const, content: "hi" }] };

const anthropic = () => new Anthropic({ apiKey: "test", fetch: vendorFetch(MESSAGE, STREAM), maxRetries: 0 });

test("anthropic create, stream and promise extras", async () => {
  const core = fakeCore();
  const sd = stardustWith(core);
  const client = instrument(anthropic(), sd);

  const msg = await client.messages.create(ARGS);
  assert.equal(msg.id, "msg_1"); // vendor object comes back untouched

  const stream = await client.messages.create({ ...ARGS, stream: true });
  const types: string[] = [];
  for await (const event of stream) types.push(event.type);
  assert.deepEqual([types[0], types.at(-1)], ["message_start", "message_stop"]);

  // APIPromise extras still work through the wrapper, and are metered too.
  const { data, response } = await client.messages.create(ARGS).withResponse();
  assert.equal(data.id, "msg_1");
  assert.equal(response.status, 200);

  await sd.flush(2000);
  assert.deepEqual(
    core.events.map((e) => [e.tokens_in, e.tokens_out, e.tokens_cached_in]),
    [[400, 20, 300], [400, 42, 300], [400, 20, 300]],
  );
});

test("API errors reach the caller and never become unhandled rejections", async () => {
  const unhandled: unknown[] = [];
  const onUnhandled = (e: unknown) => unhandled.push(e);
  process.on("unhandledRejection", onUnhandled);
  try {
    const failing = (async () =>
      new Response(JSON.stringify({ type: "error", error: { type: "api_error", message: "down" } }), {
        status: 500, headers: { "content-type": "application/json" },
      })) as unknown as typeof globalThis.fetch;
    const core = fakeCore();
    const sd = stardustWith(core);
    const client = instrument(new Anthropic({ apiKey: "test", fetch: failing, maxRetries: 0 }), sd);

    await assert.rejects(client.messages.create(ARGS), Anthropic.InternalServerError);
    await assert.rejects(client.messages.create(ARGS).withResponse(), Anthropic.InternalServerError);
    await new Promise((r) => setTimeout(r, 50));
    assert.deepEqual(unhandled, []);
    assert.equal(core.events.length, 0);
  } finally {
    process.off("unhandledRejection", onUnhandled);
  }
});

test("instrumenting twice meters once", async () => {
  const core = fakeCore();
  const sd = stardustWith(core);
  const client = instrument(instrument(anthropic(), sd), sd);
  await client.messages.create(ARGS);
  await sd.flush(2000);
  assert.equal(core.events.length, 1);
});

test("openai chat, stream with include_usage, and region override", async () => {
  const chat = {
    id: "c1", object: "chat.completion", created: 1, model: "gpt-4o-2024-08-06",
    choices: [{ index: 0, message: { role: "assistant", content: "hi" }, finish_reason: "stop" }],
    usage: { prompt_tokens: 1000, completion_tokens: 50, total_tokens: 1050, prompt_tokens_details: { cached_tokens: 600 } },
  };
  const chunk = { id: "c1", object: "chat.completion.chunk", created: 1, model: "gpt-4o-2024-08-06" };
  const stream = sse([
    { ...chunk, choices: [{ index: 0, delta: { content: "hi" }, finish_reason: null }] },
    { ...chunk, choices: [], usage: { prompt_tokens: 10, completion_tokens: 5, total_tokens: 15 } },
    "[DONE]",
  ], false);
  const core = fakeCore();
  const sd = stardustWith(core);
  const client = instrument(new OpenAI({ apiKey: "test", fetch: vendorFetch(chat, stream), maxRetries: 0 }), sd, { region: "eastus" });

  const messages = [{ role: "user" as const, content: "hi" }];
  await client.chat.completions.create({ model: "gpt-4o", messages });
  for await (const _ of await client.chat.completions.create({ model: "gpt-4o", messages, stream: true, stream_options: { include_usage: true } })) {
    // drain
  }
  await sd.flush(2000);
  assert.deepEqual(core.events.map((e) => [e.tokens_in, e.tokens_out, e.tokens_cached_in ?? null, e.region]), [
    [1000, 50, 600, "eastus"],
    [10, 5, null, "eastus"],
  ]);
});

test("gemini generateContent and generateContentStream", async () => {
  const response = (prompt: number, out: number) => ({
    modelVersion: "gemini-2.5-flash",
    usageMetadata: { promptTokenCount: prompt, candidatesTokenCount: out },
  });
  const client = {
    models: {
      async generateContent(_: unknown) { return response(10, 3); },
      async generateContentStream(_: unknown) {
        return (async function* () { yield response(10, 1); yield response(10, 4); })();
      },
    },
  };
  const core = fakeCore();
  const sd = stardustWith(core);
  instrument(client, sd);
  await client.models.generateContent({ model: "gemini-2.5-flash" });
  for await (const _ of await client.models.generateContentStream({ model: "gemini-2.5-flash" })) {
    // drain
  }
  await sd.flush(2000);
  assert.deepEqual(core.events.map((e) => [e.provider, e.tokens_in, e.tokens_out]), [["google", 10, 3], ["google", 10, 4]]);
});

test("unknown client is rejected", () => {
  const sd = stardustWith(fakeCore());
  assert.throws(() => instrument({}, sd), TypeError);
});
