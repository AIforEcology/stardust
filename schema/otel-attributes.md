# Stardust OpenTelemetry attributes

How Stardust data maps onto OpenTelemetry (spec §11). Where OTel's GenAI semantic conventions already define a usage attribute, Stardust uses it (§11.1). Everything environmental goes under the `stardust.*` namespace until the Green Software Foundation's SCI-for-OpenTelemetry conventions are ratified (§11.2); the plan is to migrate to their names then.

Stardust Core **receives** OTLP/HTTP traces at `POST /v1/traces` and **exports** a `stardust.impact` span for every enriched event.

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

## Loop safety

Core never ingests a span that has `stardust.event_id`, comes from `service.name = stardust-core`, or is named `stardust.impact`. That makes it safe to export into a collector pipeline that also feeds Core's receiver.
