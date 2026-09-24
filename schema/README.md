# Stardust schema

The open standard shared by every part of Stardust (spec §17.1). Anyone can emit or consume Stardust-compatible data by following these files.

| File | What it defines | Spec |
|---|---|---|
| [`usage-event.schema.json`](usage-event.schema.json) | A raw usage event a client sends to Core | §7 |
| [`enriched-event.schema.json`](enriched-event.schema.json) | The event after Core adds cost, impact, indicator code and job ID | §5, §7, §8 |
| [`esc.json`](esc.json) | Energy Source Codes and their fallback codes | §5.3.1–§5.3.2 |
| [`remediation.schema.json`](remediation.schema.json) | Provider registration, catalog, quotes and orders | §10.2–§10.5 |
| [`provider-telemetry.schema.json`](provider-telemetry.schema.json) | Carbon-capture provider telemetry, keyed by 3-letter field codes | §20 |
| [`otel-attributes.md`](otel-attributes.md) | How Stardust maps onto OpenTelemetry: `gen_ai.*` in, `gen_ai.*` + `stardust.*` out | §11 |
| [`factors/methodology-v0.1.json`](factors/methodology-v0.1.json) | Versioned factors: energy per token, grid intensity and mix, water, indicator thresholds, right-sizing weights | §5.1, §8, §21.4 |

## Methodology v0.1 is a draft

Every factor carries a citation. Entries marked `needs_verification` are placeholders until the board and technical contributors review them; don't present them as authoritative. In particular:

- **Indicator thresholds** (A–F grade, cost tier, volume class) are static placeholders. The spec calls for grading against a rolling benchmark once real usage data exists (§19).
- **Grid factors** are static annual averages for a few regions. The live path (Electricity Maps / WattTime, §8.6) will replace them.
- **Particulates and e-waste** are not modeled yet.

## Two v0.1 extensions to the spec

- **Cost tier `X`**: the indicator uses `X` when the model has no known price (e.g. `CX-M`). The spec only defines tiers 1–5.
- **Coal** has no Energy Source Code yet (§5.3.1), so coal generation in a grid mix is recorded as `FPP`.

## Job identifier

`job_sji` looks like `3F2A9C10-HYD-4E1A-9C3D-2B6F1A0E7C55`: a GUID shape whose second segment is the Energy Source Code. It is **not** an RFC 4122 UUID; `event_id` stays the standard UUID (§5.3).
