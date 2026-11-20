# Stardust Python SDK (`aifore-stardust`)

Meter Anthropic, OpenAI and Gemini API usage, see what it cost and what it cost the planet (electricity, CO₂e, water, and an indicator code such as `B2-S`), and route the CO₂e to vetted carbon-removal providers, through [Stardust Core](https://github.com/AIforEcology/stardust/blob/main/middleware/README.md). Token counts come from each API's own usage data, so they are measured, not estimated (spec §12.1). A project of [AI for Ecology](https://aifore.org).

- **Python 3.10+.** No dependencies: standard library only.
- **Never in your way:** events send from a background thread. Recording never blocks or raises, and a metering bug never breaks your API call.
- **Offline-tolerant:** events buffer in memory (10,000 by default, oldest dropped first) and retry with backoff while Core is unreachable.
- **Usage only:** it sends token counts, model, region and time. Never prompts or responses.

## Quick start (hosted service)

1. **Get a key.** Email AIforE at developers@aifore.org; ask for a sandbox key to start. See the [developer guide](https://github.com/AIforEcology/stardust/blob/main/docs/developers.md) for live and sandbox keys.
2. **Install:**

   ```bash
   pip install aifore-stardust
   ```
3. **Set the key:**

   ```bash
   export STARDUST_API_KEY=sdk_...
   ```
4. **Instrument your client:**

   ```python
   import anthropic
   from stardust_sdk import Stardust, instrument

   stardust = Stardust(region="us-east-1")             # https://core.aifore.org, key from STARDUST_API_KEY
   client = instrument(anthropic.Anthropic(), stardust)

   client.messages.create(model="claude-sonnet-5", max_tokens=1024, messages=[...])  # metered
   ```
5. **Read the summary:**

   ```python
   stardust.flush(5)                       # a short script: wait for events to send
   s = stardust.summary()
   print(s.cost_usd, s.energy_wh, s.co2e_g, s.water_ml, s.indicator_code)
   ```
6. **Try remediation with a sandbox key:**

   ```python
   [quote, *_] = stardust.request_quote(co2e_g=s.co2e_g)
   order = stardust.submit_order(quote)
   print(stardust.order_status(order).notice)
   # Sandbox test order: no carbon was removed and no money moved.
   ```

The import name is `stardust_sdk` (the PyPI name is `aifore-stardust`).

## Auto-instrument a client

| Client | Metered methods |
|---|---|
| `anthropic.Anthropic` / `AsyncAnthropic` | `messages.create`, including `stream=True` |
| `openai.OpenAI` / `AsyncOpenAI` | `chat.completions.create`, `responses.create`, including streams |
| `google.genai.Client` | `models.generate_content`, `models.generate_content_stream`, and the `client.aio` versions |

`instrument()` returns the same client with its methods wrapped in place. Responses and streams come back as the vendor's own objects; streams are wrapped in a pass-through proxy.

**OpenAI streaming:** chat-completion streams only include usage if you ask for it with `stream_options={"include_usage": True}`. The SDK doesn't add this for you, because it changes the stream's last chunk.

## Record manually

```python
stardust.record_response(message)                     # auto-detects Anthropic / OpenAI / Gemini, SDK objects or raw JSON
stardust.record("anthropic", "claude-sonnet-5", tokens_in=1200, tokens_out=300, tokens_cached_in=800)
```

## OpenTelemetry

Pass `otel=True` to use your app's global tracer provider, or pass a `TracerProvider`. Each metered call then also becomes a standard GenAI client span, for example `chat claude-sonnet-5` with `gen_ai.*` attributes, timed across the real call (including streams). It nests under whatever span is active and flows through your own OTel pipeline. This needs `pip install "aifore-stardust[otel]"`.

```python
stardust = Stardust(otel=True)          # direct to Core + spans
stardust = Stardust(None, otel=True)    # spans only: your collector forwards them to Core
```

Direct events reuse the span's ids, so an event that reaches Core both ways is counted once, and Core's `stardust.impact` span lands in the same trace. Don't also enable a separate GenAI instrumentation for the same client, or each call is traced twice. The span attributes are listed in [`schema/otel-attributes.md`](https://github.com/AIforEcology/stardust/blob/main/schema/otel-attributes.md).

## Reading results and remediation

These mirror Core's REST API and are separate from metering. Because you call them on purpose, they raise `StardustError` (with `.status` and `.reason`) when something goes wrong.

| Method | Calls | Returns |
|---|---|---|
| `summary(since=None, until=None)` | `GET /v1/summary` | `Summary`: events, tokens, `cost_usd`, `energy_wh`, `co2e_g`, `water_ml`, `indicator_code` |
| `summary_daily(since=None, until=None)` | `GET /v1/summary/daily` | `List[DailySummary]`, one per UTC day |
| `request_quote(co2e_g, tier_preference=None)` | `POST /v1/remediation/quotes` | `List[Quote]`, cheapest first, with the provider price and AIforE's disclosed fee |
| `submit_order(quote_id, payment_ref=None)` | `POST /v1/remediation/orders` | `Order` |
| `order_status(order_id)` | `GET /v1/remediation/orders/{id}` | `Order`, with `fulfillment_status` and `certificate` |

`since` and `until` take a `datetime` (naive means UTC), a `date` or an ISO string. Results are scoped to your key's organization (or user). With a sandbox key, every result carries `sandbox=True` and orders carry a `notice` that no carbon was removed and no money moved. Fields Core adds later are kept in each result's `raw` dict.

## Options

`Stardust(api_base=<default>, *, api_key=None, user_id=None, org_id=None, region=None, enabled=True, max_buffer=10_000, timeout=5.0, on_result=None, otel=False)`

- `api_base`: Core's URL. Defaults to `STARDUST_API_BASE`, else `https://core.aifore.org`. `None` sends nothing directly (needs `otel`).
- `api_key`: sent as `Authorization: Bearer <key>`. Defaults to `STARDUST_API_KEY`. It never appears in logs, `repr()` or errors. If Core refuses it (401 or 403), the SDK logs one warning, "Stardust: API key missing or invalid; events are being dropped", and drops events without retrying. Network errors and 5xx are retried as usual.

- `region`: the processing region, which picks the grid factors (e.g. `us-east-1`, `eu-north-1`). `instrument(client, stardust, region=...)` overrides it per client.
- `on_result`: called with Core's enriched event (indicator code, cost, CO₂e…) for each event sent.
- `flush(timeout)` waits for the queue to drain; `close()` flushes and stops. Both also run at interpreter exit (2 s limit).

## How tokens are counted

| Provider | `tokens_in` | `tokens_out` | `tokens_cached_in` |
|---|---|---|---|
| Anthropic | `input_tokens` + cache reads + cache writes | `output_tokens` (includes thinking) | cache reads |
| OpenAI | `prompt_tokens` / `input_tokens` | `completion_tokens` / `output_tokens` (includes reasoning) | `cached_tokens` |
| Gemini | `prompt_token_count` + tool-use prompt | `candidates_token_count` + `thoughts_token_count` | `cached_content_token_count` |

Core prices cached tokens at the cache-read rate. Energy is still modeled on all input tokens, which is conservative.

## Develop

From a clone of [the repository](https://github.com/AIforEcology/stardust):

```bash
pip install -e "subscriber-client/sdk-python[dev]"   # adds pytest and the vendor SDKs, for tests only
pytest subscriber-client/sdk-python
```

Licensed under Apache-2.0.
