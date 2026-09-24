# Stardust Python SDK

Meter Anthropic, OpenAI and Gemini API usage and report it to [Stardust Core](../../middleware/). Token counts come from each API's own usage data, so they are measured, not estimated (spec §12.1).

- **No dependencies:** standard library only.
- **Never in your way:** events send from a background thread. Recording never blocks or raises, and a metering bug never breaks your API call.
- **Offline-tolerant:** events buffer in memory (10,000 by default, oldest dropped first) and retry with backoff while Core is unreachable.
- **Usage only:** it sends token counts, model, region and time. Never prompts or responses.

## Install

```bash
pip install -e subscriber-client/sdk-python
```

## Auto-instrument a client

```python
import anthropic
from stardust_sdk import Stardust, instrument

stardust = Stardust("http://localhost:8080", region="us-east-1")
client = instrument(anthropic.Anthropic(), stardust)

client.messages.create(model="claude-sonnet-5", max_tokens=1024, messages=[...])  # metered
```

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

## Options

`Stardust(api_base, *, user_id=None, org_id=None, region=None, enabled=True, max_buffer=10_000, timeout=5.0, on_result=None)`

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

## Tests

```bash
pip install -e "subscriber-client/sdk-python[dev]"   # adds pytest and the vendor SDKs, for tests only
pytest subscriber-client/sdk-python
```
