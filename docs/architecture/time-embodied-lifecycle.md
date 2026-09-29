# Time-of-use, embodied carbon and hardware lifecycle

The architecture for spec v1.3 §21.7 (time-of-use and carbon-aware load shifting), §8.8.8 (component-level embodied carbon and refresh cycles) and §22.8 (the hardware lifecycle record), with the §11.5 harmonization rules they share: what they add, how they fit today's code, and the incremental plan.

**Status:** designed, not built. The field codes are in [`schema/field-codes.json`](../../schema/field-codes.json), and the planned OTel names are in [`schema/otel-attributes.md`](../../schema/otel-attributes.md#planned-mappings-115).

## What v1.3 adds

### Time-of-use and load shifting (§21.7)

Where time changes cost or emissions, Stardust records **when** energy was used, not only how much. This shows the value of moving batch training, fine-tuning and other asynchronous work to hours when the grid is cleaner or cheaper, or when renewable output would otherwise be curtailed.

| Code | Field | Unit |
|---|---|---|
| `TOU` | Time-of-use period (off-peak … critical-peak, real-time) | enum |
| `TRT` | Tariff rate at time | $/kWh |
| `GSG` | Grid signal type (marginal or average) and source (WattTime, Electricity Maps, grid operator) | enum |
| `GFW` | Grid forecast window: lowest forecast intensity in the allowed window, **as forecast at decision time** | gCO₂e/kWh, time |
| `RCF` | Renewable curtailment in the grid region | MWh or flag |
| `SLK` | Scheduling slack the owner declared **before** scheduling | minutes |
| `D-LSS` | Load-shift carbon savings | gCO₂e |
| `D-LSC` | Load-shift cost savings | $ |
| `D-CRU` | Curtailment utilization | % |

The method rules (§21.7.2):

- **Declared before, measured after.** Savings count only for jobs with `SLK` declared before scheduling, measured against the requested start time. This prevents hindsight claims.
- **Same signal on both sides.** Marginal signals for scheduling decisions and average signals for inventory reporting (GHG Protocol Scope 2). `D-LSS` always uses the same `GSG` for baseline and actual.
- **Forecast integrity.** `GFW` keeps the forecast available at decision time.
- **An explicit cost and carbon policy.** Each workload is carbon-first, cost-first or weighted, and `D-LSS` and `D-LSC` are always shown side by side.
- `D-LSS` and `D-CRU` feed the carbon timing sub-score `D-CTS` (§21.3).

Sources: WattTime (marginal) and Electricity Maps (average) through the GSF Carbon Aware SDK conventions; tariffs from OpenADR 3.0 price events, the OpenEI Utility Rate Database and Green Button interval data.

### Component-level embodied carbon (§8.8.8)

`HEE` (§8.8.2) treats a device as one embodied value. The split is uneven (NVIDIA attributes about 42% of the HGX H100 baseboard's footprint to memory), so Stardust breaks it down by component where data allows. Refresh cycles are the second lever: extending service life from 3 to 5 years cuts embodied emissions per hour of use by 40%.

| Code | Field | Unit |
|---|---|---|
| `EMC` | Embodied carbon by component, keyed by OTel `hw.type` (plus accelerator subcomponents: logic die, HBM stack, package and substrate, board) | kgCO₂e |
| `EMS` | Source of each `EMC`: manufacturer PCF (ISO 14067), open model, literature or default | enum |
| `MAT` | Material declaration (IEC 62474) | kg by substance |
| `DIE` | Die area and process node | mm², nm |
| `HBC` | HBM capacity | GB |
| `DSD` | Deployment start date | date |
| `RCP` | Refresh cycle plan | years |
| `ASL` | Actual service life | years |
| `D-EMH` | Embodied per useful hour: Σ`EMC` ÷ (life in hours × `HUR`). Supersedes `HEE` where component data exists | gCO₂e/h |
| `D-LXS` | Life-extension savings | gCO₂e/h, % |

**License note:** Boavizta's data can be used as an open model through its API, but its code is AGPL and must not be copied into this Apache-2.0 repository.

### Hardware lifecycle record (§22.8)

A passport for each device, from deployment through reuse, refurbishment and recycling. Much of this can't be traced automatically yet, so the record is interactive: operators maintain it, and refurbishers and recyclers confirm it.

| Code | Field | Unit |
|---|---|---|
| `AID` | Asset identity (manufacturer, model, part and serial number), from Redfish or the operator's asset system | string |
| `LCS` | Lifecycle state: in service, redeployed, refurbished, parts harvested, recycled, disposed | enum |
| `LCT` | State transition time | timestamp |
| `OWN` | Custodian | ID |
| `RDP` | Redeployment path | enum |
| `RMR` | Recovered material rate | % |
| `PRV` | Provenance: operator-reported, recycler-certified, manufacturer take-back, inferred | enum |
| `D-CIR` | Hardware circularity rate (ISO 59020) | % |

How records are maintained (§22.8.2):

- **Import first.** Records are seeded from Redfish, DCIM or CMDB exports, so operators confirm rather than type.
- **Prompted updates.** As an asset approaches its `RCP` date, the operator confirms its next state through the dashboard, the API or the operator MCP server ([mcp.md](mcp.md)).
- **Confirmation raises confidence.** Operator-reported records stay at the lowest confidence tier until a certified recycler or refurbisher confirms them with a chain-of-custody reference (`COC`).
- **Positive impact.** Confirmed reuse feeds `D-LXS`; confirmed recycling feeds `RCY` provider flows and `D-MAV` (§22.4).

Standards: DMTF Redfish and OTel `host.*` for identity, IEC 62474 for materials, ISO 59020 for circularity, R2 and e-Stewards for recyclers, and later the EU Digital Product Passport.

## How it maps onto today's code

| v1.3 concept | Today | Gap |
|---|---|---|
| Grid intensity (`GCI`) | Static annual average per region from the methodology file, exported as `stardust.grid.intensity_g_per_kwh` | Hourly values; the live path (§8.6); `GSG` recorded with each value |
| Time of use | Every event has a timestamp | `TOU`, `TRT`; a tariff source |
| Load shifting | None | Job-level records with `SLK` and `GFW`; `D-LSS`, `D-LSC`, `D-CRU` |
| Embodied carbon | None (`EMB` is impact-credits step 2) | `EMB` from `HEE`, then per-component `EMC` and `D-EMH` |
| Hardware assets | None | An asset table and lifecycle states |
| OTel hardware metrics | Core reads GenAI spans only | Read `hw.*` metrics (from Kepler, DCGM, Redfish) as an energy input |

The **Materials** dimension in [impact-credits.md](impact-credits.md) gets its inputs from here: `MAT`, `RMR` and `D-CIR` are what make materials measurable.

## Incremental plan

The [ground rules in impact-credits.md](impact-credits.md#ground-rules-for-every-step) apply: additive only, migrations add, new behavior behind a switch, versioned methodology, one small PR per step with docs.

| # | Step | What changes | What stays untouched |
|---|---|---|---|
| T0 | **Docs** (spec v1.4 update) | This doc; field codes in `schema/field-codes.json`; planned OTel names | All code |
| T1 | **Record the grid signal** | `GSG` on each event (the static factors are `average` from a named source); an optional hour-of-use bucket. Migration: new nullable columns | `co2e_g` and existing figures |
| T2 | **Live grid intensity** | A grid-signal adapter (Electricity Maps or WattTime, via the Carbon Aware SDK conventions), behind `STARDUST_GRID_SIGNAL` (default: static factors) | Events computed with static factors |
| T3 | **Job records and load shifting** | A job record with requested start, actual start, `SLK`, `GFW` and policy; `D-LSS` and `D-CRU` computed only for jobs with `SLK` declared in advance | Per-call events |
| T4 | **Tariffs** | `TOU` and `TRT` from an OpenADR or OpenEI source, or a tariff the operator configures; `D-LSC` | Carbon path |
| E1 | **Embodied carbon, device level** | `EMB` from `HEE`, `HLF` and `HUR` in a new methodology version (this is also impact-credits step 2) | Methodology v0.2 events |
| E2 | **Embodied carbon, component level** | `EMC` and `EMS` per `hw.type`; `D-EMH` replaces the device value where component data exists; `D-LXS` | Device-level `EMB` where no component data exists |
| L1 | **Asset store** | Asset and lifecycle tables; import from Redfish or CSV through the API; `list` and `get` endpoints. Admin-only at first | Everything else |
| L2 | **Lifecycle updates** | Propose and confirm endpoints with an audit log; `PRV`; recycler confirmation with `COC` | Asset reads |
| L3 | **Circularity** | `D-CIR` per quarter; confirmed recycling feeds the materials dimension and `RCY` flows | |
| L4 | **Operator MCP server** | See [mcp.md](mcp.md), step M5 | |
| O1 | **OTel alignment** | Emit the planned `stardust.*` interim attributes; read `hw.*` metrics as an energy input; propose the neutral names upstream | Existing attributes |

Steps T1, E1 and L1 have no open questions and can go first. T2 and T4 need a data source, and possibly an account or API key with a provider. T2 also needs a decision on marginal versus average as the default.
