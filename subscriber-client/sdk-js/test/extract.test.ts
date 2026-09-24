import assert from "node:assert/strict";
import { test } from "node:test";

import { detectProvider, extract, fromAnthropic, fromGemini, fromOpenAI } from "../src/extract.ts";

test("anthropic adds cache reads and writes to input", () => {
  const u = fromAnthropic({
    type: "message", model: "claude-sonnet-5",
    usage: { input_tokens: 100, output_tokens: 20, cache_read_input_tokens: 300, cache_creation_input_tokens: 50 },
  });
  assert.deepEqual(u, { provider: "anthropic", model: "claude-sonnet-5", tokensIn: 450, tokensOut: 20, tokensCachedIn: 300 });
});

test("openai chat and responses", () => {
  const chat = fromOpenAI({ object: "chat.completion", model: "gpt-4o", usage: { prompt_tokens: 1000, completion_tokens: 50, prompt_tokens_details: { cached_tokens: 600 } } });
  assert.deepEqual(chat, { provider: "openai", model: "gpt-4o", tokensIn: 1000, tokensOut: 50, tokensCachedIn: 600 });
  const resp = fromOpenAI({ object: "response", model: "gpt-5", usage: { input_tokens: 200, output_tokens: 80, input_tokens_details: { cached_tokens: 0 } } });
  assert.deepEqual(resp, { provider: "openai", model: "gpt-5", tokensIn: 200, tokensOut: 80, tokensCachedIn: undefined });
});

test("gemini adds thinking tokens to output", () => {
  const u = fromGemini({ modelVersion: "gemini-2.5-pro", usageMetadata: { promptTokenCount: 500, candidatesTokenCount: 100, thoughtsTokenCount: 250, cachedContentTokenCount: 200 } });
  assert.deepEqual(u, { provider: "google", model: "gemini-2.5-pro", tokensIn: 500, tokensOut: 350, tokensCachedIn: 200 });
});

test("detection and missing usage", () => {
  assert.equal(detectProvider({ type: "message", usage: {} }), "anthropic");
  assert.equal(detectProvider({ usage: { prompt_tokens: 1 } }), "openai");
  assert.equal(detectProvider({ usageMetadata: {} }), "google");
  assert.equal(extract({ hello: "world" }), undefined);
  assert.equal(extract(null), undefined);
  assert.equal(fromGemini({ usageMetadata: { promptTokenCount: 1 } }, "gemini-2.5-flash")?.model, "gemini-2.5-flash");
});
