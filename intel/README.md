# Data Center Intelligence (DCI), Phase 1 prototype

DCI replaces Stardust Core's flat US grid (367 g CO₂/kWh, methodology 0.2.0) with a footprint per provider, built from the data center facilities that provider is estimated to run on. It answers three questions per provider: where its compute runs, what powers those sites, and where the carbon lands. It follows the *Stardust Data Center Intelligence (DCI) Specification v0.1* (2026-10-21).

This prototype gets its facilities from the [Data Center Resource Ledger](../ledger/README.md), not from a hand-built registry. The ledger's facility records, the power plants linked to them and their on-site power observations feed the footprint directly.

> **Off and unpublished.** Core uses a footprint only when `STARDUST_FOOTPRINT_FILE` is set, and `deploy/` doesn't set it. Nothing DCI builds is published until the board signs off the allocation method and counsel reviews the right-of-reply process ([`docs/TODO.md`](../docs/TODO.md#data-center-intelligence-dci-gates-before-any-footprint-goes-live)).

## How a footprint is built

```text
allocations.json ──► each provider's compute split into estimated, sourced weights
        │
ledger (API or DB) ──► the facilities behind each weight: state, MW, tenant,
        │              power plants that serve them, on-site power observations
        │
grids.json (EIA) ──► each state's mix and CO₂ intensity; Core's US grid for the rest
        │
        ▼
footprint.json ──► one fp:<provider> grid per provider, plus the working and caveats
        │
        ▼
Core (STARDUST_FOOTPRINT_FILE) ──► events with no region use it, tagged estimated
```

The intensity is the weighted sum `I_provider = Σ w_f · I_grid(f)`, with `Σ w_f = 1`.

- **Allocations** ([`data/allocations.json`](data/allocations.json)) are AIforE's estimates, since no provider says which site served a request. Each component has a weight, a rationale and sources, and is labelled estimated. Compute in undisclosed locations gets its own weight on the national grid (Core's `US`), so the gap stays visible. A component names its facilities by ledger ID (`facility_id`) or, until the ID is known, by a search (`match: {q, state}`) that must find exactly one data center. A component with several facilities splits its weight by their operational MW when the ledger has every MW figure, and equally otherwise.
- **Grids** ([`data/grids.json`](data/grids.json)) come from the EIA Open Data API v2. A grid's intensity is EIA's reported CO₂ rate for that state and year where one exists. Otherwise it's computed from the generation mix with EIA combustion factors (coal 1,043, oil 1,088, gas 440 g/kWh; zero for nuclear and renewables), and marked estimated. Mixes use Stardust's Energy Source Codes (coal as `FPP`, like Core). Pumped storage is left out because its net generation is negative.
- **Caveats** name every gap: compute at undisclosed sites, facilities the search didn't find, facilities with no grid record yet, on-site generation that's reported but not quantified, facilities whose ledger tenant doesn't name the provider, and ledger facilities that list the provider as a tenant but have no weight.

The committed grids file holds Indiana and Tennessee for 2025 (EIA, retrieved 2026-11-05). Indiana's mix-computed 2024 figure (627 g/kWh) is within 1% of EIA's reported 632 g/kWh. New York, Mississippi, Pennsylvania and Texas aren't fetched yet, so those sites fall back to the US grid, and the footprint says so.

## Commands

```bash
pip install -e "intel[dev]"

stardust-intel validate                          # allocations and grids are well formed (CI runs this)
EIA_API_KEY=... stardust-intel eia-state NY TX --year 2025   # add state grids to data/grids.json
stardust-intel footprint -o footprint.json       # build from the public ledger API
stardust-intel footprint -o footprint.json --check   # exit 1 if footprint.json is stale
```

- **Ledger source:** the public API at `https://core.aifore.org/ledger/v1/...` by default (`--ledger-url` to change it). On the server, `--ledger-dsn` or `LEDGER_DATABASE_URL` reads the ledger's database instead. That needs the ledger package (`pip install -e ledger -e "intel[db]"`, Python 3.11+), whose own query functions return the same records as the API; a CI test checks that both give the same footprint.
- **EIA key:** free from <https://www.eia.gov/opendata/register.php>. It's sent to EIA but never written to the grids file.
- **`--check`** ignores `generated_on` and the ledger's facility count, which change without changing any number.

The ledger is empty until the sheet import runs, so a footprint built today places nothing and puts every provider on the US grid with a caveat for each component.

## Footprint file

`"format": "stardust-footprint"`, read by Core's [`footprint.py`](../middleware/src/stardust_core/footprint.py):

| Key | |
|---|---|
| `dci_version` | DCI version; Core reports `<methodology>+dci-<version>`, e.g. `0.2.0+dci-0.1.0` |
| `core_methodology_version` | The methodology the footprint was built against. Core refuses a mismatch |
| `grids` | `fp:<provider>` → `intensity_g_per_kwh`, `mix` (ESC → %), `confidence` (`estimated`), `citation` |
| `provider_default_grid` | provider → grid. Core refuses one that names a grid it doesn't define |
| `providers` | The working: components, weights, facilities and their grids, power plants, on-site power, `known_site_share`, `vs_flat_pct`, `caveats`, `notes` |
| `as_of`, `generated_on`, `status`, `ledger` | Provenance |

## Tests

`pytest intel`. The EIA tests replay responses recorded from `api.eia.gov` on 2026-11-05 ([`tests/recorded/eia/`](tests/recorded/eia/)), including a rate-limit error. The footprint tests use a small synthetic ledger. `tests/test_intel_ledger_db.py` runs in CI's ledger job against PostgreSQL + PostGIS: it writes facilities through the real ledger API and checks that the API and database readers give the same footprint.

## Not built yet (from the spec)

- AI extraction of facts from documents, with a human review queue (`stardust-intel extract`, `review`). The ledger's agents and review status now cover that path.
- Hourly balancing-authority mixes and marginal emissions; PPAs and market-based emissions (Phase 2).
- Facility-specific water and heat (Phases 2 and 3). The ledger already has a water table to draw on.
- The Markdown report (`stardust-intel report`) and the public map (Phase 3).
