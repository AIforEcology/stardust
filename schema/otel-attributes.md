# Stardust OpenTelemetry attributes

How Stardust data maps onto OpenTelemetry (spec §11). Where OTel's GenAI semantic conventions already define a usage attribute, Stardust uses it (§11.1). Everything environmental goes under the `stardust.*` namespace until the Green Software Foundation's SCI-for-OpenTelemetry conventions are ratified (§11.2); the plan is to migrate to their names then.

Stardust Core **receives** OTLP traces over HTTP (`POST /v1/traces`) and gRPC, and **exports** a `stardust.impact` span per enriched event plus aggregated metrics, over HTTP or gRPC. The Stardust SDKs can also **emit** standard GenAI spans themselves.

## What Core reads from incoming spans

A span becomes a Stardust usage event when it carries `gen_ai.usage.input_tokens` or `gen_ai.usage.output_tokens`. Other spans are ignored. Span attributes override resource attributes.

| Attribute | Used as |
|---|---|
| `gen_ai.provider.name` (or legacy `gen_ai.system`) | `provider`. `gcp.gemini`, `gcp.vertex_ai` → `google`; `azure.ai.openai` → `openai` |
| `gen_ai.response.model`, else `gen_ai.request.model` | `model` |
| `gen_ai.usage.input_tokens` | `tokens_in` |
| `gen_ai.usage.output_tokens` | `tokens_out` |
| `gen_ai.usage.cache_read.input_tokens` | `tokens_cached_in` |
| `cloud.region` | `region`, which selects the grid factors |
| `stardust.source_layer` (optional) | `source_layer`, default `infra_agent` |
| `stardust.user_id`, `stardust.org_id` (optional, UUIDs) | pseudonymous ids (§14.1) |

- **Cached tokens:** current semconv counts cached tokens inside `input_tokens`. If cache reads exceed `input_tokens`, the instrumentation clearly excluded them, so Core adds cache reads and writes back into the input total.
- **Idempotent:** `event_id` is a UUIDv5 of the span's trace and span ids, so a collector retry never double counts.
- **Partial success:** GenAI spans with unusable data (e.g. negative token counts) are reported back as `partial_success.rejected_spans`.

## What Core emits

One `stardust.impact` span (kind `INTERNAL`) per enriched event, from resource `service.name = stardust-core`. For events that arrived over OTLP, it's a **child of the original GenAI span**, timed at that span's end, so it appears in the same trace.

| Attribute | Type | Meaning |
|---|---|---|
| `gen_ai.provider.name` | string | Provider |
| `gen_ai.request.model` | string | Model |
| `gen_ai.usage.input_tokens` | int | Input tokens, including cached |
| `gen_ai.usage.output_tokens` | int | Output tokens |
| `gen_ai.usage.cache_read.input_tokens` | int | Cached input tokens |
| `cloud.region` | string | Processing region. It's a span attribute because Core's resource isn't the workload's |
| `stardust.event_id` | string | Stardust event UUID |
| `stardust.source_layer` | string | `chat_plugin` / `browser_ext` / `infra_agent` |
| `stardust.job.sji` | string | Stardust Job Identifier (§5.3) |
| `stardust.tokens.estimated` | bool | Tokens were estimated, not read from a usage payload |
| `stardust.cost.usd` | double | Cost; **omitted** when unknown |
| `stardust.impact.energy_wh` | double | Electricity |
| `stardust.impact.co2e_g` | double | Greenhouse gases, CO₂e |
| `stardust.impact.water_ml` | double | Water |
| `stardust.impact.confidence_tier` | string | `measured` / `modeled` / `estimated` (§8) |
| `stardust.indicator.code` | string | e.g. `B2-M` (§5.1) |
| `stardust.esc.code` | string | Energy Source Code (§5.3.1) |
| `stardust.grid.style` | string | `dedicated` / `grid-blend` |
| `stardust.grid.majority_share_pct` | double | Plurality source's share of the mix |
| `stardust.grid.diversified` | bool | Mix too even to name a dominant source |
| `stardust.grid.intensity_g_per_kwh` | double | Grid carbon intensity applied |
| `stardust.grid.mix.<ESC>` | double | Share of each source in the mix, e.g. `stardust.grid.mix.NGP` |
| `stardust.model.tier` | string | `small` / `mid` / `frontier` / `unknown` |
| `stardust.methodology.version` | string | Factor set used, for auditability |

## Trace context on direct events

Events sent straight to `POST /v1/events` can carry `otel_trace_id` (32 lowercase hex) and `otel_span_id` (16 lowercase hex). Core then exports its `stardust.impact` span into that trace. The SDKs set these, and set `event_id` to the same UUIDv5 of the trace and span ids that the receiver uses. An event that reaches Core both directly and through a collector is therefore counted once.

## Spans the SDKs emit

With OpenTelemetry on (`Stardust(otel=True)` in Python, `new Stardust({ tracer })` in JS), each metered call is a GenAI client span following the semantic conventions:

| | |
|---|---|
| Name | `{gen_ai.operation.name} {model}`, e.g. `chat claude-sonnet-5` or `generate_content gemini-2.5-flash` |
| Kind | `CLIENT`, timed from the call to the response or the stream's last chunk |
| Attributes | `gen_ai.operation.name`, `gen_ai.provider.name` (`anthropic`, `openai`, `gcp.gemini`), `gen_ai.request.model`, `gen_ai.response.model`, `gen_ai.usage.input_tokens` (includes cached), `gen_ai.usage.output_tokens`, `gen_ai.usage.cache_read.input_tokens`, `cloud.region`, `stardust.source_layer`, `stardust.user_id`, `stardust.org_id` |
| Errors | Status `ERROR`, `error.type`, and an `exception` event |

## Metrics Core emits

Monotonic counters, exported every `STARDUST_OTLP_METRICS_INTERVAL` seconds (cumulative temporality unless the exporter's standard `OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE` says otherwise):

| Metric | Unit | Extra attributes |
|---|---|---|
| `stardust.ai.requests` | `{request}` | `stardust.cost.known` |
| `stardust.ai.tokens` | `{token}` | `gen_ai.token.type` = `input` (includes cached) / `output` |
| `stardust.ai.tokens.cached` | `{token}` | |
| `stardust.cost` | `USD` | only operations with a known price |
| `stardust.energy` | `Wh` | |
| `stardust.co2e` | `g` | |
| `stardust.water` | `mL` | |

Every metric carries `gen_ai.provider.name`, `gen_ai.request.model`, `cloud.region` (`unknown` if not set), `stardust.source_layer`, `stardust.model.tier`, `stardust.impact.confidence_tier`, `stardust.esc.code` and `stardust.indicator.grade` (A–F), plus `stardust.org_id` when present. User and event ids are never metric attributes.

## Loop safety

Core never ingests a span that has `stardust.event_id`, comes from `service.name = stardust-core`, or is named `stardust.impact`. That makes it safe to export into a collector pipeline that also feeds Core's receiver.
