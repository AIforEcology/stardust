# Stardust JS/TS SDK (`@aifore/stardust`)

Meter Anthropic, OpenAI and Gemini API usage from Node.js (18+), see what it cost and what it cost the planet (electricity, CO₂e, water, and an indicator code such as `B2-S`), and route the CO₂e to vetted carbon-removal providers, through [Stardust Core](https://github.com/AIforEcology/stardust/blob/main/middleware/README.md). It has no runtime dependencies and uses the global `fetch`. It works the same way as the [Python SDK](https://github.com/AIforEcology/stardust/blob/main/subscriber-client/sdk-python/README.md): measured token counts, background sending, retries while Core is down, usage only (never prompts or responses). A project of [AI for Ecology](https://aifore.org).

## Quick start (hosted service)

1. **Get a key.** Email AIforE at developers@aifore.org; ask for a sandbox key to start. See the [developer guide](https://github.com/AIforEcology/stardust/blob/main/docs/developers.md) for live and sandbox keys.
2. **Install:** `npm i @aifore/stardust`
3. **Set the key:** `export STARDUST_API_KEY=sdk_...`
4. **Instrument your client:**

   ```ts
   import Anthropic from "@anthropic-ai/sdk";
   import { Stardust, instrument } from "@aifore/stardust";

   const stardust = new Stardust({ region: "us-east-1" }); // https://core.aifore.org, key from STARDUST_API_KEY
   const client = instrument(new Anthropic(), stardust);

   await client.messages.create({ model: "claude-sonnet-5", max_tokens: 1024, messages: [/* … */] }); // metered
   ```
5. **Read the summary:**

   ```ts
   await stardust.flush(5000); // a short script: wait for events to send
   const s = await stardust.summary();
   console.log(s.cost_usd, s.energy_wh, s.co2e_g, s.water_ml, s.indicator_code);
   ```
6. **Try remediation with a sandbox key:**

   ```ts
   const [quote] = await stardust.requestQuote(s.co2e_g);
   const order = await stardust.submitOrder(quote);
   console.log((await stardust.orderStatus(order)).notice);
   // Sandbox test order: no carbon was removed and no money moved.
   ```

## Use

| Client | Metered methods |
|---|---|
| `@anthropic-ai/sdk` | `messages.create`, including `stream: true` |
| `openai` | `chat.completions.create`, `responses.create`, including streams |
| `@google/genai` | `models.generateContent`, `models.generateContentStream` |

- **Vendor extras still work:** vendor promise helpers such as `.withResponse()` keep working, and those calls are metered too.
- **OpenAI streaming:** chat-completion streams only report usage with `stream_options: { include_usage: true }`.
- **Manual recording:** `stardust.recordResponse(response)` or `stardust.record({ provider, model, tokensIn, tokensOut, tokensCachedIn })`.
- **Short-lived processes:** retries never keep Node alive, so call `flush()` or `close()` before exiting.

## OpenTelemetry

Pass a tracer from `@opentelemetry/api`, and each metered call also becomes a standard GenAI client span, for example `chat claude-sonnet-5` with `gen_ai.*` attributes, timed across the real call (including streams). The SDK only uses the tracer you give it; `@opentelemetry/api` is an optional peer dependency, not a runtime import.

```ts
import { trace } from "@opentelemetry/api";

new Stardust({ tracer: trace.getTracer("my-app") });                // direct + spans
new Stardust({ apiBase: null, tracer: trace.getTracer("my-app") }); // spans only, via your collector
```

Direct events reuse the span's ids, so Core counts each call once, however it arrives. Attributes are listed in [`schema/otel-attributes.md`](https://github.com/AIforEcology/stardust/blob/main/schema/otel-attributes.md).

## Reading results and remediation

These mirror Core's REST API and are separate from metering. Because you call them on purpose, they reject with `StardustError` (with `.status` and `.reason`) when something goes wrong.

| Method | Calls | Resolves to |
|---|---|---|
| `summary({ since?, until? })` | `GET /v1/summary` | `Summary`: events, tokens, `cost_usd`, `energy_wh`, `co2e_g`, `water_ml`, `indicator_code` |
| `summaryDaily({ since?, until? })` | `GET /v1/summary/daily` | `DailySummary[]`, one per UTC day |
| `requestQuote(co2eG, tierPreference?)` | `POST /v1/remediation/quotes` | `Quote[]`, cheapest first, with the provider price and AIforE's disclosed fee |
| `submitOrder(quote, paymentRef?)` | `POST /v1/remediation/orders` | `Order` |
| `orderStatus(order)` | `GET /v1/remediation/orders/{id}` | `Order`, with `fulfillment_status` and `certificate` |

`since` and `until` take a `Date` or an ISO string. Results are scoped to your key's organization (or user). With a sandbox key, every result carries `sandbox: true`, and orders carry a `notice` that no carbon was removed and no money moved.

## Options

`new Stardust({ apiBase, apiKey, region, userId, orgId, tracer, enabled, maxBuffer, timeoutMs, onResult, fetch })`

- `apiBase`: defaults to `STARDUST_API_BASE`, else `https://core.aifore.org`. `null` sends nothing directly (needs `tracer`).
- `apiKey`: sent as `Authorization: Bearer <key>`. Defaults to `STARDUST_API_KEY`. It's kept in a private field, so it never appears in logs, `console.log`, `JSON.stringify` or errors. If Core refuses it (401 or 403), the SDK logs one warning, "Stardust: API key missing or invalid; events are being dropped", and drops events without retrying. Network errors and 5xx are retried as usual.

## Develop

`npm run build` (to `dist/`), `npm run typecheck`, `npm test`. Licensed under Apache-2.0.
