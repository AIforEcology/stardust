# Stargrain SDKs for developers

The Stargrain SDKs (part of the Omnigauge Suite) meter your app's AI usage and tells you what it cost and what it cost the planet: electricity, CO₂e and water, summed up as a short indicator code such as `B2-S`. It can also route the CO₂e to vetted carbon-removal providers. You add the SDK, point it at the hosted service at `https://core.aifore.org` with an API key, and keep your own LLM calls exactly as they are.

- **Python 3.10+:** [`aifore-stargrain`](https://pypi.org/project/aifore-stargrain/) ([README](../subscriber-client/sdk-python/README.md))
- **JavaScript / TypeScript (Node 18+):** [`@aifore/stargrain`](https://www.npmjs.com/package/@aifore/stargrain) ([README](../subscriber-client/sdk-js/README.md))

## 1. Get a key

For now, email AIforE at **developers@aifore.org** to request a key. Say what you're building and whether you want a sandbox key, a live key or both. Self-serve sign-up comes later.

You get a key that looks like `sdk_<key_id>_<secret>`. It's shown once: AIforE stores only a hash of it. Keep it in an environment variable or your secret manager, never in code or a repository.

| Key | For | What it does |
|---|---|---|
| **Sandbox** | Building and testing your integration | Your usage is kept apart from live data and never counts toward any real totals. Remediation reaches only the **Stardust Sandbox Provider (test only)**: no carbon is removed and no money moves. Every sandbox quote, order and certificate says `"sandbox": true`. |
| **Live** | Production | Real totals. Remediation quotes come from vetted real providers. |

Sandbox and live never mix. A sandbox key can't reach a real provider, a live key never sees the sandbox provider, and data sent with one isn't visible to the other.

Keys carry scopes. A developer key normally has `ingest.write` (send usage), `reporting.read` (read your totals) and `remediation.write` (quotes and orders). A key belongs to one organization (and optionally one user in it), and it only ever sees that organization's data.

## 2. Install, set the key, instrument your client

```bash
pip install aifore-stargrain          # or: npm i @aifore/stargrain
export STARGRAIN_API_KEY=sdk_...      # your key
```

```python
import anthropic
from stargrain import Stargrain, instrument

stargrain = Stargrain(region="us-east-1")              # https://core.aifore.org, key from STARGRAIN_API_KEY
client = instrument(anthropic.Anthropic(), stargrain)  # OpenAI and Gemini clients work the same way
client.messages.create(model="claude-sonnet-5", max_tokens=1024, messages=[...])  # metered
```

```ts
import Anthropic from "@anthropic-ai/sdk";
import { Stargrain, instrument } from "@aifore/stargrain";

const stargrain = new Stargrain({ region: "us-east-1" }); // key from STARGRAIN_API_KEY
const client = instrument(new Anthropic(), stargrain);
```

Metering never gets in your way: events are sent from the background, recording never throws, and if Core is unreachable events are buffered and retried. If your key is missing or wrong, the SDK logs one warning ("Stargrain: API key missing or invalid; events are being dropped") and carries on without sending.

`STARGRAIN_API_BASE` overrides the service URL, for example to run against your own Core. The older `STARDUST_API_KEY` and `STARDUST_API_BASE` names still work as fallbacks.

## 3. Read your results

```python
s = stargrain.summary()              # or summary(since=..., until=...)
print(s.cost_usd, s.energy_wh, s.co2e_g, s.water_ml, s.indicator_code)
for day in stargrain.summary_daily():
    print(day.day, day.co2e_g)
```

```ts
const s = await stargrain.summary();
const days = await stargrain.summaryDaily({ since: new Date("2026-11-01") });
```

Totals cover your key's organization (or user). With a sandbox key they cover your sandbox test data only, and the result says `sandbox: true`.

## 4. Remediation (try it with a sandbox key)

```python
quotes = stargrain.request_quote(co2e_g=s.co2e_g)   # cheapest first
order = stargrain.submit_order(quotes[0])
status = stargrain.order_status(order)
print(status.fulfillment_status, status.sandbox, status.notice)
# fulfilled True Sandbox test order: no carbon was removed and no money moved.
```

```ts
const [quote] = await stargrain.requestQuote(s.co2e_g);
const order = await stargrain.submitOrder(quote);
const status = await stargrain.orderStatus(order);
```

- A quote is valid for 15 minutes and can be used once.
- Unlike metering, these calls are ones you make on purpose, so they raise (`StargrainError`) or reject on failure.
- Placing a live order with a real provider involves payment (`payment_ref`); talk to AIforE before you go live with remediation.

### Provider tiers

Every quote names the provider's tier, assigned by AIforE when the provider is vetted (spec §10.2.1, §10.6). Providers don't set their own tier.

| Tier | Meaning |
|---|---|
| `verified` | An active Verra or Gold Standard registration |
| `emerging` | Vetted by AIforE, without one of those registrations |
| `engineered` | Engineered removal, such as direct air capture |

Pass `tier_preference` (Python) or the second argument of `requestQuote` (JS) to limit quotes to some tiers.

### The tech operations fee

AIforE charges a disclosed **tech operations fee** on remediation orders, to pay for the software, hosting, provider vetting and verification. It's a separate line from the provider's price, which the provider sets and receives in full. Every quote shows `price_usd` (the provider's), `fee_pct`, `fee_usd` and `total_usd`, and the fee on a quote is the one charged. The current terms are public:

```bash
curl -s https://core.aifore.org/v1/fees/terms
```

## The indicator code

Each event, and each summary, carries a code like `B2-S`: **impact grade**, **cost tier**, then **token volume class** (spec §5.1).

| Part | Values | From |
|---|---|---|
| Impact grade | `A` (lowest) to `F` | CO₂e per 1,000 tokens (or per operation, without tokens) |
| Cost tier | `1` (cheapest) to `5`, `X` if the model's price is unknown | Cost of the operation in USD |
| Volume class | `XS`, `S`, `M`, `L`, `XL` | Tokens in the operation |

A summary grades pooled CO₂e per 1,000 tokens, and uses the average operation's cost and size. The exact thresholds and every factor behind the figures are public at [`/v1/methodology`](https://core.aifore.org/v1/methodology). The factors are a draft (methodology 0.2) and are labelled with their confidence tier.

## What data is sent

**Usage only, never prompts or responses.** Each event carries the provider, model, token counts (input, output, cached input), the region you set, a timestamp, and trace ids if you use OpenTelemetry. Your key decides which organization (and user) it's recorded under. Events are kept for two years on the hosted service, and a user's events can be erased on request ([privacy@aifore.org](mailto:privacy@aifore.org)).

## Rate limits

There's no per-key rate limit on the hosted service yet; please keep to what your app actually uses, and talk to AIforE before sending very high volumes. If limits are added, Core answers `429` and the SDK backs off and retries on its own; the reading and remediation helpers raise instead, so you can retry later. The SDK buffers up to 10,000 events in memory (configurable) and drops the oldest first if Core stays unreachable.

## Check it end to end

[`scripts/e2e_sandbox.py`](../scripts/e2e_sandbox.py) runs the whole flow with both SDKs against any Core: it sends three metered events, checks the summary went up, then quotes, orders and polls a sandbox order to fulfilled, checking `sandbox: true` on the quote, order and certificate.

```bash
STARGRAIN_API_KEY=sdk_... python3 scripts/e2e_sandbox.py          # Python SDK, against https://core.aifore.org
STARGRAIN_API_KEY=sdk_... node scripts/e2e_sandbox.mjs            # JavaScript SDK (after npm run build in sdk-js)
```

## More

- The full REST API, scopes and sandbox rules: [`middleware/README.md`](../middleware/README.md)
- OpenTelemetry: both SDKs can also emit standard GenAI spans ([attributes](../schema/otel-attributes.md))
- The spec: [`docs/`](README.md) (v1.7) and its [summary](stardust-summary.pdf)
