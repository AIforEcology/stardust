#!/usr/bin/env node
// End-to-end check of the JavaScript SDK against a Stardust Core, with a sandbox key.
//
//   STARDUST_API_KEY=sdk_... node scripts/e2e_sandbox.mjs    # STARDUST_API_BASE defaults to hosted Core
//
// The same steps as scripts/e2e_sandbox.py: 3 metered events from recorded fake usage, the summary goes up,
// then a sandbox quote, order and status poll, checking "sandbox": true on each. Refuses a live key.
// Uses the SDK built in this repository (cd subscriber-client/sdk-js && npm ci && npm run build).

import { Stardust, StardustError } from "../subscriber-client/sdk-js/dist/index.js";

const ANTHROPIC_MESSAGE = { type: "message", model: "claude-sonnet-4-5", role: "assistant",
  usage: { input_tokens: 1200, output_tokens: 350, cache_read_input_tokens: 0 } };
const OPENAI_COMPLETION = { object: "chat.completion", model: "gpt-4o-mini",
  usage: { prompt_tokens: 800, completion_tokens: 200, total_tokens: 1000 } };

function check(ok, what) {
  console.log(`${ok ? "  ok    " : "  FAIL  "}${what}`);
  if (!ok) process.exit(1);
}

async function main() {
  if (!process.env.STARDUST_API_KEY) {
    console.log("Set STARDUST_API_KEY to a sandbox key (and STARDUST_API_BASE for a Core other than the hosted one).");
    return 2;
  }
  const accepted = [];
  const sd = new Stardust({ region: "us-east-1", onResult: (r) => accepted.push(r) });
  console.log(`Stardust Core: ${sd.apiBase} (JavaScript SDK)`);

  const before = await sd.summary();
  if (before.sandbox !== true) {
    console.log("This is a live key. The end-to-end check places an order, so it only runs with a sandbox key.");
    return 2;
  }
  check(true, `sandbox key; summary before: ${before.events} events, ${before.co2e_g.toFixed(6)} g CO2e`);

  sd.recordResponse(ANTHROPIC_MESSAGE);
  sd.recordResponse(OPENAI_COMPLETION);
  sd.record({ provider: "google", model: "gemini-2.5-flash", tokensIn: 400, tokensOut: 120 });
  check(await sd.flush(30_000), "3 events sent");
  check(accepted.length === 3 && sd.dropped === 0, `Core accepted all 3 (${accepted.length} accepted, ${sd.dropped} dropped)`);
  check(accepted.every((e) => e.sandbox === true), "each enriched event says sandbox");

  const after = await sd.summary();
  check(after.events >= before.events + 3, `summary events went up: ${before.events} -> ${after.events}`);
  check(after.co2e_g > before.co2e_g, `summary CO2e went up: ${before.co2e_g.toFixed(6)} -> ${after.co2e_g.toFixed(6)} g`);
  check(after.energy_wh > before.energy_wh && after.water_ml > before.water_ml, "energy and water went up");
  check(Boolean(after.indicator_code), `indicator code: ${after.indicator_code}`);
  check((await sd.summaryDaily()).length >= 1, "daily summary has at least one day");

  const co2eG = Math.max(after.co2e_g - before.co2e_g, 1);
  const quotes = await sd.requestQuote(co2eG);
  check(quotes.length >= 1, `${quotes.length} quote(s) for ${co2eG.toFixed(4)} g`);
  check(quotes.every((q) => q.sandbox === true), "every quote is sandbox");
  const order = await sd.submitOrder(quotes[0], `e2e-sandbox-js-${Date.now()}`);
  check(order.sandbox === true, `order ${order.remediation_order_id} is sandbox`);
  check(Boolean(order.notice), `order notice: ${order.notice}`);

  const deadline = Date.now() + 60_000;
  let status = await sd.orderStatus(order);
  while (["ordered", "fulfilling"].includes(status.fulfillment_status) && Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, 2000));
    status = await sd.orderStatus(order);
  }
  check(status.fulfillment_status === "fulfilled", `order status: ${status.fulfillment_status}`);
  check(status.sandbox === true, "order status is sandbox");
  check(status.certificate?.sandbox === true, `certificate is sandbox: ${status.certificate?.ref}`);
  await sd.close(5000);
  console.log("Passed.");
  return 0;
}

main().then((code) => process.exit(code), (e) => {
  console.log(`  FAIL  ${e instanceof StardustError ? e.message : e}`);
  process.exit(1);
});
