# Stardust JS/TS SDK

Meter Anthropic, OpenAI and Gemini API usage from Node.js (18+) and report it to [Stardust Core](../../middleware/). It has no runtime dependencies and uses the global `fetch`. It works the same way as the [Python SDK](../sdk-python/): measured token counts, background sending, retries while Core is down, usage only.

## Use

```ts
import Anthropic from "@anthropic-ai/sdk";
import { Stardust, instrument } from "@stardust/sdk";

const stardust = new Stardust({ apiBase: "http://localhost:8080", region: "us-east-1" });
const client = instrument(new Anthropic(), stardust);

await client.messages.create({ model: "claude-sonnet-5", max_tokens: 1024, messages: [/* … */] }); // metered
await stardust.flush(2000); // before a short-lived script exits
```

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

new Stardust({ apiBase: "http://localhost:8080", tracer: trace.getTracer("my-app") }); // direct + spans
new Stardust({ apiBase: null, tracer: trace.getTracer("my-app") });                     // spans only, via your collector
```

Direct events reuse the span's ids, so Core counts each call once, however it arrives. Attributes are listed in [`schema/otel-attributes.md`](../../schema/otel-attributes.md).

## Scripts

`npm run build` (to `dist/`), `npm run typecheck`, `npm test`
