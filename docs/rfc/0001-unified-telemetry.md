# RFC 0001: Unified Telemetry Specification for Real-Time AI Inference Energy Accounting (v0.1)

AI for Ecology (AIforE.org) · Draft v0.1 · October 2026

## 1. Status and abstract

**Status:** Working draft for public comment. Published by AI for Ecology (AIforE.org), a Washington State 501(c)(3) nonprofit. Licensed under CC BY 4.0, with reference code under Apache 2.0. Derived from Stardust Telemetry Specification T1.5 (master v1.6). Sections marked "proposed for v0.2" are listed in Appendix A.

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
- Inference that runs on edge devices and workstations, and multi-step agent tasks (proposed for v0.2)

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

### 3.1 Deployment profiles (proposed for v0.2)

Conformance levels say how deep an implementation reports. Deployment profiles say where the inference runs, which sets what can be measured and how telemetry leaves the device. The two are independent: a local workstation reading its own power counters can report at L2, and a cloud tenant can report at L1.

| Profile | Where inference runs | Typical energy source | Export path |
| --- | --- | --- | --- |
| cloud | Hyperscaler or managed API | Provider disclosure, or modeled | Continuous OTLP |
| colocation | Tenant hardware in a third-party facility | Node and rack power (NPW, RKP); operator PUE and WUE | Continuous OTLP |
| on\_premises | Organization-owned data center | Node, rack and facility meters (§5.2) | Continuous OTLP |
| edge | Embedded or field device with its own accelerator (NPU, SoC) | Device energy counters, or modeled from the device's rated power | Buffered, batch sync (§3.2) |
| workstation | Developer or end-user machine running a local model | Whole-device energy (DVE) from RAPL, NVML or the OS energy counters | Buffered, batch sync (§3.2) |

| Code | Attribute (interim) | Type | Req. | Notes |
| --- | --- | --- | --- | --- |
| DPF | stardust.deployment.profile | enum | MUST | One of the profiles above. A call to a remote API made from a workstation has profile cloud: the profile follows the inference, not the caller. |
| DVE | stardust.device.energy\_j | float | SHOULD for edge and workstation | Whole-device energy over the interval, from a cumulative counter (§5.1). Intel and AMD RAPL, NVML for discrete GPUs, and the operating system's energy counters on other platforms. Reported alongside OTel hw.energy where a collector emits it. |
| PWS | stardust.device.power\_state | enum | SHOULD for edge and workstation | ac or battery. While on battery, OTel hw.battery.charge is reused for state of charge. |
| GZN | stardust.grid.zone | string | MUST for edge and workstation when no cloud.region applies | The grid zone or balancing authority whose factors apply, for example an Electricity Maps zone. Set by configuration or derived from a coarse region. It MUST NOT be derived from precise device location. |

**Battery-powered intervals.** Energy drawn from a battery was taken from the grid when the battery charged. An interval with power state battery uses the grid factor from the most recent charging period when the device recorded one. Otherwise it uses the zone's daily average, with factor.basis historical\_average and confidence estimated.

**Local models.** gen\_ai.provider.name carries the local runtime (for example ollama or llama.cpp) and gen\_ai.request.model the model file it loaded. QNT (numeric precision) SHOULD be set, because quantization changes energy per token far more on consumer hardware than in a data center.

### 3.2 Buffered export for edge and workstation profiles (proposed for v0.2)

Edge devices and workstations are often offline, metered or bandwidth-limited. They MAY hold events and export them in batches, under these rules:

1. **Time of use, not time of export.** interval.start and interval.end are the time the energy was used. Factors are matched to that time (§4.1), never to the time the batch arrived.
2. **Bounded buffer.** The buffer has a fixed size. When it is full, the oldest events are dropped and the drop is counted, so the gap shows up in the record rather than disappearing.
3. **Idempotent retry.** Every event keeps a stable event id from the moment it is created, so a batch sent twice is counted once.
4. **No content.** The buffer holds the same fields as any other event. Prompt text, response text, prompt embeddings and token probability distributions MUST NOT be buffered, consistent with §2.
5. **Clock source.** Devices without a reliable clock record where their time came from, so intervals that cannot be matched to an hourly factor drop to the nearest coarser one (§4.1, rule 1).

| Code | Attribute (interim) | Type | Req. | Notes |
| --- | --- | --- | --- | --- |
| EXL | stardust.export.lag\_s | float | MUST when buffered | Seconds between interval.end and export |
| BFL | stardust.export.buffer\_dropped | int | MUST when buffered | Events dropped from a full buffer since the previous successful export, carried on the first event of each batch |
| CLK | stardust.device.clock\_source | enum | SHOULD when buffered | ntp, gnss, rtc (unsynchronized) or unknown |

**Aggregate-only export.** A device MAY export aggregated metrics instead of per-call events, for privacy or bandwidth. If it adds noise for differential privacy, it MUST state the mechanism and its epsilon, and the noised values carry confidence estimated.

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

### 4.2 Agent and workflow attribution (proposed for v0.2)

An agent task is many model calls and tool calls. Per-call events alone cannot show what a finished task cost, or how much energy went into retries and loops that produced nothing. This section attributes energy to the task.

**Reuse the OTel agent spans.** The OTel GenAI conventions already define invoke\_workflow, invoke\_agent, plan and execute\_tool spans, with gen\_ai.workflow.name, gen\_ai.agent.id, gen\_ai.agent.name, gen\_ai.tool.name, gen\_ai.tool.type and gen\_ai.tool.call.id. Conforming implementations use those spans and names as they are. The events in §4 are the model calls beneath them in the same trace.

**Roll-up.** The outermost invoke\_workflow or invoke\_agent span of a task carries the sums of its descendants' energy\_wh, co2e\_g and water\_ml, and the confidence tier of the weakest of them. Energy for a tool's own execution (a code sandbox, a search API) is included only when it was measured or modeled with a stated method. Otherwise the roll-up states that it covers model calls only.

| Code | Attribute (interim) | Type | Req. | Notes |
| --- | --- | --- | --- | --- |
| TSK | stardust.task.outcome | enum | MUST on the task's root span at L2 | completed, failed, abandoned (stopped by the user or a budget) or handed\_off (to a person). Only completed tasks count toward UOC. |
| STP | stardust.task.step\_count | int | SHOULD | Model calls and tool calls under the task |
| TCA | stardust.tool.call.attempt | int | SHOULD on execute\_tool spans | Attempt number for the same gen\_ai.tool.call.id, starting at 1 |
| LPD | stardust.task.loop\_detected | enum | SHOULD on the task's root span | none, repeated\_call (the same tool with the same arguments hash, three or more times in a row), cycle (a repeating sequence of steps) or step\_limit (stopped by a maximum step count) |
| D-AWE | stardust.task.wasted\_energy\_wh | float | MUST on the task's root span at L2 | Energy spent on failed tool attempts, steps inside a detected loop, and every step of a task whose outcome is not completed |

The arguments hash used to detect repeated calls is computed and compared on the device or host. The arguments themselves are content and MUST NOT be exported. gen\_ai.input.messages, gen\_ai.output.messages, gen\_ai.tool.call.arguments and gen\_ai.tool.call.result stay off, as §2 requires.

**Useful output for agents.** For the agentic workload class, this section proposes an answer to Q1: one UOC is one task whose root span has outcome completed. D-AWE is reported alongside D-EIE, so an agent cannot look efficient by finishing a few tasks cheaply while abandoning many others.

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

The exact definition of a "useful output" for each workload class is open question Q1 in Section 8. §4.2 proposes a definition for agentic tasks.

### 5.1 Measuring energy per token (proposed for v0.2)

Prompt processing takes tens to hundreds of milliseconds. Sampling power every 1–10 seconds cannot separate it from generation, so a D-EPT split built that way is a model, not a measurement. This section sets how D-EPT is measured so that values from different operators can be compared.

**Counters before samples.** Energy MUST come from a cumulative energy counter where the hardware has one: AEC for accelerators (the NVML and DCGM total-energy counter, AMD SMI energy count), CPE for CPU and memory (RAPL), and OTel hw.energy where a collector emits it. Read at the start and end of an interval, a counter captures every joule in between regardless of how short the phases inside it were. Integrating sampled power (APW, NPW) is allowed only where no counter exists. A per-phase value built from sampled power is measured only if the sampling rate was 10 Hz or faster. Otherwise it is modeled. A counter delta that is negative or crosses a driver reset MUST be discarded, not clamped.

**Two methods.** Every D-EPT value MUST state which method produced it (EPB).

- **Benchmark.** Controlled runs for one model × hardware × precision × batch-size band. Prompt-processing energy comes from runs that generate a single token. Generation energy comes from long-generation runs, minus their prompt-processing share. Each configuration is repeated at least five times, and the mean is reported with its 95% confidence interval. The device's idle power is measured first and subtracted, and reported separately.
- **Production attribution.** For each interval (60 seconds or shorter, and no longer than the finest factor resolution in use), the device's counter delta is split between prompt processing and generation in proportion to the tokens the engine processed in each phase (from the vLLM, SGLang or TensorRT-LLM token counters), weighted by the ratio of the two from the benchmark for that band. It is then split across requests by their tokens, and across tenants by partition share (PTN). The split is modeled even though the total is measured, until per-request hardware energy counters are available (T1.5 §21.9.6).

| Code | Attribute (interim) | Type | Req. | Notes |
| --- | --- | --- | --- | --- |
| EPB | stardust.ept.basis | enum | MUST with D-EPT | benchmark or production |
| EPS | stardust.ept.source | enum | MUST with D-EPT | counter, or power\_sampled with the sampling rate in Hz |
| IDE | stardust.ept.idle\_energy\_j | float | MUST with D-EPT at L3 | Energy used in the interval while no sequence was running. It is reported separately and never folded into per-token figures. Long idle periods are a provisioning signal (IDC, D-STR), not an inference cost. |

### 5.2 Facility power and on-site supply (proposed for v0.2)

T1.5 §21.9.3 already defines rack power (RKP), node power (NPW), inlet temperature (INL), coolant loop state (CLT) and cooling headroom (CHD). Two gaps remain: how these readings are collected in facilities that run building-management protocols, and where the rack's power actually came from in a given interval.

**Collection paths.** Facility readings MAY arrive over DMTF Redfish, IPMI DCMI, SNMP (power distribution unit MIBs), Modbus TCP or BACnet/IP. Each reading is registered with a sensor descriptor (T1.5 §21.9.5) stating its scope (rack, row, hall or site), its sampling interval and its accuracy. The protocol is recorded, because it limits the accuracy the reading can claim.

**Air-side cooling.** CLT covers liquid loops. Air-cooled halls need the same view of their air handlers and chilled-water plant.

| Code | Attribute (interim) | Type | Req. | Notes |
| --- | --- | --- | --- | --- |
| AHU | stardust.facility.air\_handler | object | SHOULD at L3 for air-cooled halls | Supply and return air temperature, their difference, and fan power for each computer-room air handler or air conditioner (CRAH, CRAC) |
| CHW | stardust.facility.chilled\_water | object | SHOULD at L3 where chillers serve the hall | Chilled-water supply and return temperature, their difference, flow rate and chiller power |

**On-site supply.** A rack can draw from the utility grid, on-site generation, a contracted supply, a battery or a backup generator, and the mix can change minute to minute. Using the regional grid factor for all of it overstates emissions when on-site solar is running, and understates them when diesel generators are.

| Code | Attribute (interim) | Type | Req. | Notes |
| --- | --- | --- | --- | --- |
| SSM | stardust.facility.supply\_mix | object | MUST at L3 for sites with on-site generation or storage | Share of the interval's site energy from: utility grid, on-site generation (by ESC), contracted supply such as a PPA (with its matching granularity, §4.1), battery discharge and backup generation |
| BSS | stardust.facility.battery | object | MUST when SSM includes battery discharge | Charge or discharge power, state of charge, round-trip efficiency, and the carbon intensity of the energy stored, weighted by when it was charged |
| BGR | stardust.facility.backup\_generation | object | MUST when SSM includes backup generation | Whether generators ran, their output, runtime and fuel type |

**Carbon of stored and backup energy.** Battery discharge carries the charge-weighted intensity of the energy that charged it, divided by round-trip efficiency. It MUST NOT be reported as zero-carbon unless it was charged from zero-carbon supply, matched at the same granularity (§4.1, rule 3). Backup generation uses a fuel-based emission factor, never the grid factor.

**Proposed Energy Source Codes.** Two codes are proposed for the board and technical review that governs the code table (T1.5 §5.3.1). Until they are accepted, implementations use the fallbacks.

| Code | Name | Fallback | Rule |
| --- | --- | --- | --- |
| BES | Battery energy storage discharge | UNK | Used only when the charging source is unknown. When it is known, the job takes the charging source's code instead. |
| DGS | On-site diesel or other liquid-fuel generator set | FPP | Backup or prime-power generation at the facility |

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
| Agent and tool calls (§4.2) | OTel GenAI agent spans: invoke\_workflow, invoke\_agent, plan, execute\_tool | gen\_ai.task.outcome, gen\_ai.tool.call.attempt |
| Device energy and battery (§3.1) | OTel hw.energy, hw.battery.charge; RAPL; NVML | hw.power\_source.state |
| Energy-per-token method (§5.1) | NVML and DCGM energy counters, AMD SMI, RAPL; inference-engine token counters | gen\_ai.server.energy\_per\_token, split by phase |
| Facility meters and on-site supply (§5.2) | DMTF Redfish, IPMI DCMI, SNMP, Modbus TCP, BACnet/IP | energy.site.supply\_mix, energy.storage.carbon\_intensity |

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
- [ ] Q6. Should a task handed off to a person (§4.2) count as a partial useful output, or none?
- [ ] Q7. Is 10 Hz the right threshold for calling a per-phase value built from sampled power measured (§5.1), or should only counters qualify?
- [ ] Q8. Should SSM (§5.2) apply below L3, for colocation tenants whose operator publishes the site's supply mix?

## Appendix A. Changes proposed for v0.2

These sections are proposed for v0.2 and are open for comment with the rest of the draft. None of them changes a v0.1 requirement.

- §3.1 Deployment profiles: cloud, colocation, on-premises, edge and workstation, independent of conformance level, with device energy, power state and grid zone.
- §3.2 Buffered export for edge and workstation profiles, including dropped-event counts and a ban on buffering content.
- §4.2 Agent and workflow attribution on the OTel agent spans, task outcome, wasted energy, and a proposed answer to Q1 for agentic tasks.
- §5.1 How energy per token is measured: counters before power samples, the benchmark and production methods, and separate idle energy.
- §5.2 Facility collection paths, air-side cooling, on-site supply mix, battery and backup-generation carbon, and two proposed Energy Source Codes (BES, DGS).
- §7 Four new mapping rows, and Q6–Q8.

New fields use interim stardust.\* names (§7). Their three-character codes do not collide with any code in T1.5.

**Source.** The full field registry, more than 180 coded fields including Provider telemetry, is in the Stardust Telemetry Specification T1.5 (from master v1.6), in the AIforE shared Drive docs folder.
