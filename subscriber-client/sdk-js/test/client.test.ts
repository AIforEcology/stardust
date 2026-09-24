import assert from "node:assert/strict";
import { test } from "node:test";

import { fakeCore, stardustWith } from "./helpers.ts";

const U = { provider: "anthropic" as const, model: "claude-sonnet-5", tokensIn: 100, tokensOut: 20, tokensCachedIn: 50 };

test("records and sends an event", async () => {
  const core = fakeCore();
  const sd = stardustWith(core);
  sd.record(U);
  assert.equal(await sd.flush(2000), true);
  const [e] = core.events;
  assert.equal(e.source_layer, "infra_agent");
  assert.equal(e.tokens_estimated, false);
  assert.deepEqual([e.tokens_in, e.tokens_out, e.tokens_cached_in, e.region], [100, 20, 50, "us-east-1"]);
  assert.match(String(e.timestamp), /[+-]\d\d:\d\d$/);
  assert.equal("user_id" in e, false);
});

test("onResult gets the enriched event", async () => {
  const results: unknown[] = [];
  const sd = stardustWith(fakeCore(), { onResult: (r: unknown) => results.push(r) });
  sd.record(U);
  await sd.flush(2000);
  assert.equal((results[0] as { indicator_code: string }).indicator_code, "B2-S");
});

test("retries while Core is down, quickly during flush", async () => {
  const core = fakeCore(["down", 503, 200]);
  const sd = stardustWith(core);
  sd.record(U);
  const start = Date.now();
  assert.equal(await sd.flush(5000), true);
  assert.ok(Date.now() - start < 2000, "flush should not wait out the backoff");
  assert.equal(core.calls, 3);
  assert.equal(core.events.length, 1);
});

test("rejected events are dropped, not retried", async () => {
  const core = fakeCore([422]);
  const sd = stardustWith(core);
  sd.record(U);
  assert.equal(await sd.flush(2000), true);
  assert.equal(core.calls, 1);
  assert.equal(core.events.length, 0);
});

test("flush times out while down and keeps the event", async () => {
  const core = fakeCore(Array(1000).fill("down"));
  const sd = stardustWith(core);
  sd.record(U);
  assert.equal(await sd.flush(200), false);
  assert.equal(sd.pending, 1);
  await sd.close(0);
});

test("bounded buffer drops the oldest", async () => {
  const core = fakeCore(Array(1000).fill("down"));
  const sd = stardustWith(core, { maxBuffer: 3 });
  for (let i = 0; i < 6; i++) sd.record({ ...U, model: `m${i}` });
  assert.ok(sd.dropped >= 2);
  assert.ok(sd.pending <= 4);
  await sd.close(0);
});

test("disabled records nothing; recordResponse never throws", async () => {
  const core = fakeCore();
  const sd = stardustWith(core, { enabled: false });
  sd.record(U);
  assert.equal(await sd.flush(100), true);
  assert.equal(core.calls, 0);
  const weird = { get usage() { throw new Error("boom"); }, type: "message" };
  assert.equal(stardustWith(core).recordResponse(weird, { provider: "anthropic" }), undefined);
});
