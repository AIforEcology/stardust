import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { inspect } from "node:util";

import { AUTH_WARNING, DEFAULT_API_BASE, Stardust, StardustError } from "../src/index.ts";
import { fakeCore, stardustWith } from "./helpers.ts";

const KEY = "sdk_0123456789abcdef_s3cr3t-value-that-must-never-leak";
const U = { provider: "anthropic" as const, model: "claude-sonnet-5", tokensIn: 100, tokensOut: 20 };

let saved: Record<string, string | undefined>;
let warnings: string[];
const realWarn = console.warn;
beforeEach(() => {
  saved = { key: process.env.STARDUST_API_KEY, base: process.env.STARDUST_API_BASE };
  delete process.env.STARDUST_API_KEY;
  delete process.env.STARDUST_API_BASE;
  warnings = [];
  console.warn = (...args: unknown[]) => void warnings.push(args.map(String).join(" "));
});
afterEach(() => {
  console.warn = realWarn;
  for (const [k, name] of [["key", "STARDUST_API_KEY"], ["base", "STARDUST_API_BASE"]] as const) {
    if (saved[k] === undefined) delete process.env[name];
    else process.env[name] = saved[k];
  }
});

test("sends the key as a bearer header", async () => {
  const core = fakeCore();
  const sd = stardustWith(core, { apiKey: KEY });
  sd.record(U);
  assert.equal(await sd.flush(2000), true);
  assert.equal(core.headers[0].authorization, `Bearer ${KEY}`);
});

test("no key, no header", async () => {
  const core = fakeCore();
  const sd = stardustWith(core);
  sd.record(U);
  await sd.flush(2000);
  assert.equal("authorization" in core.headers[0], false);
});

test("key and base fall back to the environment", async () => {
  process.env.STARDUST_API_KEY = KEY;
  const core = fakeCore();
  const sd = stardustWith(core);
  sd.record(U);
  await sd.flush(2000);
  assert.equal(core.headers[0].authorization, `Bearer ${KEY}`);

  assert.equal(new Stardust({ fetch: core.fetch }).url, `${DEFAULT_API_BASE}/v1/events`);
  process.env.STARDUST_API_BASE = "http://override.test/";
  assert.equal(new Stardust({ fetch: core.fetch }).url, "http://override.test/v1/events");
});

for (const status of [401, 403]) {
  test(`a ${status} warns once and is not retried`, async () => {
    const core = fakeCore([status, status, status]);
    const sd = stardustWith(core, { apiKey: KEY });
    sd.record(U);
    sd.record(U);
    sd.record(U);
    assert.equal(await sd.flush(2000), true);
    assert.equal(core.calls, 3);
    assert.equal(core.events.length, 0);
    assert.equal(sd.dropped, 3);
    assert.equal(warnings.filter((w) => w === AUTH_WARNING).length, 1);
  });
}

test("network errors and 5xx still retry", async () => {
  const core = fakeCore(["down", 503, 200]);
  const sd = stardustWith(core, { apiKey: KEY });
  sd.record(U);
  assert.equal(await sd.flush(5000), true);
  assert.equal(core.calls, 3);
  assert.equal(core.events.length, 1);
});

test("the key never shows up in logs, inspect, JSON or errors", async () => {
  const core = fakeCore([401, 422, "down", 200]);
  const sd = stardustWith(core, { apiKey: KEY });
  sd.record(U);
  sd.record(U);
  sd.record(U);
  await sd.flush(5000);
  assert.ok(!warnings.join("\n").includes(KEY));
  assert.ok(!inspect(sd, { depth: 5 }).includes(KEY));
  assert.ok(!JSON.stringify(sd).includes(KEY));

  const unauthorized = (async () => new Response('{"detail":"invalid API key","reason":"unauthorized"}', { status: 401 })) as unknown as typeof fetch;
  const sd2 = new Stardust({ apiBase: "http://core.test", apiKey: KEY, fetch: unauthorized });
  await assert.rejects(sd2.summary(), (e: unknown) => {
    assert.ok(e instanceof StardustError);
    assert.equal(e.status, 401);
    assert.equal(e.reason, "unauthorized");
    assert.ok(!String(e).includes(KEY) && !inspect(e).includes(KEY));
    return true;
  });
});

// --- helpers ------------------------------------------------------------------------------------

type Req = { method: string; path: string; query: string; body: unknown; headers: Record<string, string> };

function fakeApi(routes: Record<string, [number, unknown]>) {
  const requests: Req[] = [];
  const fetch = (async (url: string, init: RequestInit) => {
    const u = new URL(url);
    const req = {
      method: init.method ?? "GET",
      path: u.pathname,
      query: u.search,
      body: init.body ? JSON.parse(String(init.body)) : undefined,
      headers: init.headers as Record<string, string>,
    };
    requests.push(req);
    const [status, payload] = routes[`${req.method} ${req.path}`] ?? [404, { detail: "no route" }];
    return new Response(JSON.stringify(payload), { status });
  }) as unknown as typeof globalThis.fetch;
  return { requests, sd: new Stardust({ apiBase: "http://core.test", apiKey: KEY, fetch }) };
}

const QUOTE = {
  quote_id: "11111111-1111-4111-8111-111111111111", provider_id: "mock-reforestation", project_id: "mock-project-1",
  co2e_g: 1000, price_usd: 0.02, fee_pct: 8, fee_usd: 0.0016, total_usd: 0.0216, fulfillment_days: 0,
  provider_tier: "emerging", expires_at: "2026-11-19T12:15:00+00:00", sandbox: true,
};
const ORDER = {
  remediation_order_id: "22222222-2222-4222-8222-222222222222", quote_id: QUOTE.quote_id, provider_id: "mock-reforestation",
  provider_tier: "emerging", co2e_g_offset: 1000, fulfillment_status: "fulfilled", certificate_ref: "SANDBOX-x",
  certificate: { ref: "SANDBOX-x", sandbox: true, note: "SANDBOX — not a real removal" }, total_usd: 0.0216, fee_usd: 0.0016,
  sandbox: true, notice: "Sandbox test order: no carbon was removed and no money moved.",
};

test("summary and summaryDaily", async () => {
  const { sd, requests } = fakeApi({
    "GET /v1/summary": [200, { events: 3, co2e_g: 0.5, indicator_code: "B2-S", sandbox: true }],
    "GET /v1/summary/daily": [200, { days: [{ day: "2026-11-19", events: 3 }] }],
  });
  const s = await sd.summary({ since: new Date("2026-11-01T00:00:00Z") });
  assert.equal(s.indicator_code, "B2-S");
  assert.equal(s.sandbox, true);
  assert.equal(requests[0].query, "?since=2026-11-01T00%3A00%3A00.000Z");
  assert.equal(requests[0].headers.authorization, `Bearer ${KEY}`);
  const days = await sd.summaryDaily();
  assert.equal(days[0].day, "2026-11-19");
  assert.equal(requests[1].query, "");
});

test("requestQuote, submitOrder and orderStatus", async () => {
  const { sd, requests } = fakeApi({
    "POST /v1/remediation/quotes": [200, { quotes: [QUOTE] }],
    "POST /v1/remediation/orders": [200, ORDER],
    [`GET /v1/remediation/orders/${ORDER.remediation_order_id}`]: [200, ORDER],
  });
  const [q] = await sd.requestQuote(1000, ["emerging"]);
  assert.equal(q.sandbox, true);
  assert.deepEqual(requests[0].body, { co2e_g: 1000, tier_preference: ["emerging"] });
  const order = await sd.submitOrder(q, "test-ref");
  assert.equal(order.fulfillment_status, "fulfilled");
  assert.equal(order.certificate?.sandbox, true);
  assert.deepEqual(requests[1].body, { quote_id: QUOTE.quote_id, payment_ref: "test-ref" });
  const status = await sd.orderStatus(order);
  assert.equal(status.remediation_order_id, ORDER.remediation_order_id);
});

test("helper errors reject", async () => {
  const { sd } = fakeApi({ "POST /v1/remediation/orders": [400, { detail: "unknown or already-used quote" }] });
  await assert.rejects(sd.submitOrder("q"), (e: unknown) => e instanceof StardustError && e.status === 400 && /already-used/.test(e.message));
  await assert.rejects(sd.orderStatus("nope"), (e: unknown) => e instanceof StardustError && e.status === 404);

  const down = (async () => {
    throw new TypeError("fetch failed");
  }) as unknown as typeof fetch;
  await assert.rejects(new Stardust({ apiBase: "http://core.test", fetch: down }).summary(), (e: unknown) => e instanceof StardustError && e.status === 0);
});
