import assert from "node:assert/strict";
import { test } from "node:test";

import { accountTurn, fingerprint, hiddenCharsEstimate, remember } from "../src/context.ts";
import { parseClaudeModel } from "../src/sites.ts";

test("claude.ai model labels map to API model ids", () => {
  assert.equal(parseClaudeModel("Model: Opus 5.5 Medium"), "claude-opus-5-5");
  assert.equal(parseClaudeModel("Sonnet 5"), "claude-sonnet-5");
  assert.equal(parseClaudeModel("Model: Haiku 4.5"), "claude-haiku-4-5");
  assert.equal(parseClaudeModel("Model: Fable 5.1 Extended"), "claude-fable-5-1");
  assert.equal(parseClaudeModel("Model: Something New"), undefined);
});

test("running context reproduces the live claude.ai test", () => {
  // Measured 2026-09-24: user 44 chars → reply 3,792; user 16 → reply 3,449; user 16 → reply 3,487.
  let ctx = 0;
  const t1 = accountTurn(ctx, 44, 3792); ctx = t1.nextContextChars;
  const t2 = accountTurn(ctx, 16, 3449); ctx = t2.nextContextChars;
  const t3 = accountTurn(ctx, 16, 3487);
  assert.deepEqual([t1.inputChars, t2.inputChars, t3.inputChars], [44, 3852, 7317]);
});

test("hidden turns are estimated from spacer height", () => {
  // Live page: rendered turns 6,954 chars over 3,964 px; spacer 2,284 px (actual hidden ≈ 3,852 chars).
  const est = hiddenCharsEstimate(2284, 6954, 3964);
  assert.ok(Math.abs(est - 3852) / 3852 < 0.1, `estimate ${est} should be within 10% of 3852`);
  assert.equal(hiddenCharsEstimate(0, 100, 100), 0);
  assert.equal(hiddenCharsEstimate(500, 0, 0), 0);
});

test("per-conversation totals are bounded, newest kept", () => {
  let totals: Record<string, number> = {};
  for (let i = 0; i < 5; i++) totals = remember(totals, `c${i}`, i, 3);
  assert.deepEqual(Object.keys(totals), ["c2", "c3", "c4"]);
  totals = remember(totals, "c2", 99, 3); // updating moves it to newest
  assert.deepEqual(Object.keys(totals), ["c3", "c4", "c2"]);
});

test("fingerprints identify identical replies only", () => {
  assert.equal(fingerprint("hello world"), fingerprint("hello world"));
  assert.notEqual(fingerprint("hello world"), fingerprint("hello worle"));
  assert.match(fingerprint("abc"), /^3:/);
});

import { parseChatGPTSlug, parseGeminiMode } from "../src/sites.ts";

test("ChatGPT model slugs map to API ids", () => {
  assert.equal(parseChatGPTSlug("gpt-5-6"), "gpt-5.6"); // seen live 2026-09-24
  assert.equal(parseChatGPTSlug("gpt-4-1-mini"), "gpt-4.1-mini");
  assert.equal(parseChatGPTSlug("gpt-5-mini"), "gpt-5-mini"); // not a version number: unchanged
  assert.equal(parseChatGPTSlug("gpt-4o"), "gpt-4o");
  assert.equal(parseChatGPTSlug(null), undefined);
});

test("Gemini modes map to a family name", () => {
  assert.equal(parseGeminiMode("Open mode picker, currently Flash"), "gemini-flash"); // seen live 2026-09-24
  assert.equal(parseGeminiMode("Open mode picker, currently Pro"), "gemini-pro");
  assert.equal(parseGeminiMode("Open mode picker, currently Deep Think"), "gemini-deep-think");
  assert.equal(parseGeminiMode("Mode picker"), undefined);
});
