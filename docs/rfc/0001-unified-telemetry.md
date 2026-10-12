# RFC 0001: Unified Telemetry Specification for Real-Time AI Inference Energy Accounting (v0.1)

AI for Ecology (AIforE.org) · Draft v0.1 · October 2026

## 1. Status and abstract

**Status:** Working draft for public comment. Published by AI for Ecology (AIforE.org), a Washington State 501(c)(3) nonprofit. Licensed under CC BY 4.0, with reference code under Apache 2.0. Derived from Stardust Telemetry Specification T1.5 (master v1.6).

**Abstract.** Organizations running AI inference cannot compare energy, carbon or water use across providers, because each one measures and reports differently. This RFC defines a vendor-neutral telemetry schema for inference energy accounting. Every measured AI operation is emitted as an OpenTelemetry span carrying:

- usage data (tokens, model, provider)
- energy
- lifecycle emissions, split into operational, facility, training and embodied components
- the grid signal used
- a confidence tier

The schema has three conformance levels, so a browser plugin, a cloud tenant and a data-center operator can each report at the depth their access allows. It reuses OpenTelemetry, the Green Software Foundation's SCI (ISO/IEC 21031) and ISO/IEC 30134 wherever they already apply. New attributes are proposed upstream under vendor-neutral names.

The key words MUST, SHOULD and MAY are used as defined in RFC 2119.

## 2. Motivation and scope

Today's AI benchmarks measure peak hardware speed. They do not measure the energy a real workload uses on a real grid, at a real facility, after routing decisions. Without a shared schema, routing, model selection and load shifting cannot be checked from the outside, and providers' sustainability claims cannot be compared.

**In scope**

- Per-operation inference telemetry: usage, energy, emissions, water and grid signal
- How lifecycle emissions are attributed to each call (training and embodied shares)
- Efficiency metrics: energy per token, and useful output per kWh, per kg CO2e and per dollar
- Telemetry from routers and load-shifting schedulers, so their savings can be verified
- Accelerator, node and facility signals used to measure energy instead of estimating it

**Out of scope for v0.1**

- Pass/fail certification thresholds. These come later, through the AIforE Eco-Certified program, after a public benchmarking period.
- Telemetry for carbon removal or credit Providers. This is defined in the Stardust specification §20 and §22, and will be proposed separately.
- Prompt or response content. Conforming systems MUST NOT store prompt text or prompt embeddings as part of this telemetry.

## 3. Conformance levels

The three levels set how deep an implementation reports. They do not grade how efficient it is. Each level includes everything in the levels below it.

| Level | Typical implementer | MUST emit | Typical confidence |
| --- | --- | --- | --- |
| L1 Usage | Chat plugin, browser extension, LLM gateway | Core event schema (§4) with modeled energy\_wh and co2e\_g, grid signal type (GSG), confidence\_tier, and the interval and factor-resolution attributes (§4.1) | Estimated / modeled |
| L2 Workload | Cloud tenant, inference platform, enterprise AI team | L1, plus accelerator-seconds (ACS), the lifecycle component split OPE/FAC/TRA/EMB (§5), Useful Output Count (UOC), the efficiency indexes D-EIE and D-EIG, and energy price at time (§4.1). Routers also emit the routing fields (§6). | Modeled / measured |
| L3 Infrastructure | Data-center operator, hyperscaler | L2, plus measured accelerator energy (AEC or APW), node power (NPW), interval PUE and WUE, measured energy per token (D-EPT) per model × hardware × precision, hourly or finer grid factors, and demand-charge contribution (§4.1) | Measured |

A conforming implementation MUST state its level and the specification version in every export. A field it cannot fill MUST be omitted, never zero-filled. Every derived value MUST carry the confidence tier of its weakest input.

## 4. Core event schema

Each measured operation (a chat turn, an API call or an inference request) is one event. Events are emitted as OpenTelemetry spans, or as metric data points for aggregated views.

| Attribute | Type | Req. | Notes |
| --- | --- | --- | --- |
| gen\_ai.provider.name | string | MUST | OTel GenAI convention |
| gen\_ai.request.model | string | MUST | OTel GenAI convention |
| gen\_ai.usage.input\_tokens / output\_tokens | int | MUST (if applicable) | OTel GenAI convention |
| cloud.region | string | SHOULD | OTel Resource attribute; selects the grid and facility factors |
| energy\_wh | float | MUST | Measured or modeled |
| co2e\_g | float | MUST | energy\_wh × grid intensity, plus lifecycle components at L2 and above |
| water\_ml | float | SHOULD | From interval WUE where available, otherwise defaults |
| confidence\_tier | enum | MUST | measured, modeled or estimated |
| grid.signal\_type / signal\_source | enum | MUST | Marginal or average, plus the provider of the signal (GSG) |
| energy\_source\_code | enum | SHOULD | 3-character code for the plurality energy source, e.g. HYD, NGP, or the fallbacks REP, FPP, ZEP, UNK |
| grid\_style / grid\_majority\_share\_pct | enum / float | SHOULD | Dedicated source or grid blend, and the plurality source's share of the mix |
| job\_sji | string | MAY | Human-readable Stardust Job Identifier |
| conformance\_level, spec\_version | string | MUST | Section 3 |

**Stardust Job Identifier.** The SJI uses the GUID shape XXXXXXXX-ESC-XXXX-XXXX-XXXXXXXXXXXX, with the Energy Source Code in the second segment (for example 3F2A9C10-HYD-4E1A-9C3D-2B6F1A0E7C55). It is a companion identifier meant for humans to read. It never replaces the OTel trace\_id or span\_id.

**No overstatement.** A specific plant code MUST NOT be assigned when the evidence only supports a category. If the plurality source holds less than 25% of the mix, displays MUST show the job as a mixed grid. The full list of energy source codes is in Stardust T1.5 §5.3.

### 4.1 Time and temporal matching

When energy is used changes what it costs, how much carbon it emits, and how much water it uses. Every event MUST therefore record when its energy was used, and every factor applied to it MUST be matched to that time. Factors are grid intensity, energy price, PUE and WUE.

| Attribute | Type | Req. | Notes |
| --- | --- | --- | --- |
| interval.start / interval.end | datetime (UTC) | MUST | Span start and end time. Long-running jobs are split into intervals no longer than the factor resolution. Local UTC offset retained. |
| factor.temporal\_resolution | enum | MUST | Per factor applied: realtime (5 min or less), hourly, daily, monthly or annual |
| factor.valid\_from / factor.valid\_to | datetime | MUST | The window the factor value applies to, so it can be checked against the interval |
| factor.basis | enum | MUST | measured\_at\_time, forecast\_at\_decision, or historical\_average |
| energy.price\_at\_time | decimal ($/kWh) | SHOULD (MUST at L2+) | Tariff or market price in effect for the interval, with its time-of-use period |
| cost\_usd | decimal | SHOULD | Energy cost (energy × price at time) plus API or compute spend |
| energy.demand\_charge | decimal ($/kW) + kW | MAY (SHOULD at L3) | Peak-demand rate and this workload's contribution to the billing-period peak |
| energy.matching\_granularity | enum | MUST when claiming renewable supply | hourly, monthly or annual. States whether renewable certificates match the same hour as the energy use. |

**Matching rules**

1. An event's carbon, cost and water MUST use factors whose validity window overlaps the event interval. If none exists, the nearest coarser factor is used, its resolution is recorded, and confidence drops to estimated.
2. Figures computed from annual or monthly averages MUST NOT be described as time-matched. Displays SHOULD show the resolution next to the number.
3. Renewable or carbon-free energy claims MUST state their matching granularity. Only hourly-matched claims (for example, EnergyTag granular certificates) may be described as 24/7 or time-matched.
4. Forecasts used to make a decision are stored as made at decision time, and are never replaced afterward with actual values.

## 5. Lifecycle emissions and efficiency metrics

At L2 and above, emissions MUST be reported as separate components, and their sum MUST NOT be reported alone.

```latex
\text{D-LCE} = \text{OPE} + \text{FAC} + \text{TRA} + \text{EMB} \; (+\, \text{STE})
```

| Code | Component | Basis |
| --- | --- | --- |
| OPE | Operational inference | Chip and server energy × grid intensity at run time |
| FAC | Facility overhead | OPE scaled by interval PUE (ISO/IEC 30134-2) |
| TRA | Amortized training | Training emissions (TLE) ÷ lifetime token throughput (LTT) |
| EMB | Embodied hardware | Embodied emissions by component (ISO 14067 PCF first, then Boavizta, literature, defaults) ÷ (service life × utilization) |
| STE | Self-trained | Fine-tuning on the subscriber's own infrastructure, measured |

**Energy per token (D-EPT).** Measured joules per token, split into prompt processing (prefill) and generation (decode), for each model × hardware × precision × batch-size band. It is built from hardware energy counters (AEC) or node power (NPW). It replaces static wattage tables.

**Energy per useful output.** Tokens alone reward short answers, not useful ones. The headline efficiency metrics therefore divide by Useful Output Count (UOC): completed tasks or accepted responses.

| Code | Metric | Unit |
| --- | --- | --- |
| D-EIE | Efficiency Index (Energy) | UOC/kWh |
| D-EIG | Efficiency Index (Carbon) | UOC/kgCO2e |
| D-EIC | Efficiency Index (Cost) | UOC/$ |
| D-SCI | SCI score (ISO/IEC 21031), with UOC as the functional unit | gCO2e/UOC |

The exact definition of a "useful output" for each workload class is open question Q1 in Section 8.

## 6. Routing and load-shifting telemetry

Any router or scheduler that claims energy savings MUST emit enough telemetry for those savings, its own overhead, and any loss in quality to be checked by others. This applies to every implementation, including AIforE's own open-source reference router (the Efficiency Control Plane).

**Routers MUST emit:** the tier first chosen (RTI) and the reason (RRC), predicted success (RPS), router energy (RCE), escalation events with the tokens and energy they wasted (EVT, EWT, EWE), and the policy version (RPV).

**Required derived checks:**

| Code | Check | Rule |
| --- | --- | --- |
| D-RSV | Routing energy savings | Counterfactual energy minus actual energy, including router energy and wasted escalation energy |
| D-ROR | Router overhead ratio | MUST stay at or below 0.05 over a rolling 7 days |
| D-RQR | Routing quality regret | MUST be reported alongside any savings claim |
| D-ECE | Calibration error | Target at or below 0.05, measured against opt-in shadow evaluation |

**Load shifting.** Savings count only for jobs that declared their scheduling slack (SLK) before they were scheduled. The baseline is the originally requested start time. Marginal grid signals SHOULD drive scheduling decisions, and average signals SHOULD be used for inventory reporting, consistent with GHG Protocol Scope 2. The baseline and the actual run MUST use the same signal type. Load-shift savings MUST be computed with factors at the same temporal resolution for the baseline and the actual run (§4.1). Fields: TOU, TRT, GFW, RCF, D-LSS, D-LSC, D-CRU, plus the demand-charge fields DMD, PKD and D-DCC (Stardust T1.5 §21.7).

## 7. OpenTelemetry mapping and existing standards

This RFC adds to existing standards rather than competing with them. The rule: reuse an existing convention first. Define a new attribute only where none exists, and propose it upstream under a vendor-neutral name. Until a proposal is accepted, new attributes live under the interim stardust.\* namespace.

| Concern | Existing standard reused | Proposed upstream (draft names) |
| --- | --- | --- |
| Model, provider, tokens, latency | OTel GenAI semantic conventions (gen\_ai.\*) | gen\_ai.routing.tier, .reason, .predicted\_success, .policy\_version |
| Accelerator, host and PSU energy | OTel hw.\* metrics; NVIDIA DCGM, AMD SMI, Kepler, DMTF Redfish | hw.gpu.throttle.reason, hw.gpu.power.limit |
| Embodied carbon | ISO 14067 PCFs; Boavizta; OTel hw.type | hw.embodied.co2e, hw.embodied.source, hw.lifecycle.state |
| Grid carbon intensity | WattTime (marginal), Electricity Maps (average), GSF Carbon Aware SDK | energy.grid.carbon\_intensity, .signal\_type, .signal\_source |
| Tariffs and demand response | OpenADR 3.0, OpenEI URDB, Green Button | energy.tariff.period, energy.tariff.rate |
| Software carbon intensity | GSF SCI (ISO/IEC 21031); SCI for OpenTelemetry (draft) | Contribute the lifecycle split and confidence tiers |
| Facility efficiency | ISO/IEC 30134-2 (PUE), -9 (WUE), -6 (ERF) | None. Reused as is |

**Transport.** Implementations emit standard OTLP. Stardust Core works as both an OTLP receiver and an OTLP exporter, so any existing OTel Collector or backend can store this data without a separate telemetry stack.

## 8. Governance, neutrality and comment process

**Neutrality.** AIforE edits this RFC under its Conflict of Interest and Certification Independence Policy. AIforE's own reference implementations (Stardust and the Efficiency Control Plane router) follow the same rules as everyone else. They are never certified. On leaderboards they are labeled "AIforE reference, unranked." No method in this RFC may favor their design. AIforE's router has no connection to any commercial routing product.

**Licensing.** Specification text is licensed under CC BY 4.0. Schemas, harness code and the reference implementations are licensed under Apache 2.0, in the AIforEcology GitHub organization.

**How to comment.** Open an issue or pull request in the AIforEcology/stardust repository, tagged rfc-0001. The comment period runs 60 days from publication. The Technical Steering Committee will publish how each substantive comment was resolved before v0.2.

**Open questions**

- [ ] Q1. How should a "useful output" (UOC) be defined for each workload class (chat, agentic task, batch extraction, code), and who validates it?
- [ ] Q2. Should L1 require a minimum disclosure of the method used to model energy, so estimated figures stay comparable?
- [ ] Q3. Which reference hardware footprints and test prompts should the first benchmark harness use?
- [ ] Q4. Should water\_ml become MUST at L2, given how few facilities publish interval WUE?
- [ ] Q5. Which proposed attributes should go to the OTel GenAI SIG first, and which to the GSF SCI-for-OpenTelemetry effort?

**Source.** The full field registry, more than 180 coded fields including Provider telemetry, is in the Stardust Telemetry Specification T1.5 (from master v1.6), in the AIforE shared Drive docs folder.
