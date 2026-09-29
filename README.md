# Project Stardust

**The AIforE Subscriber Plugin.** Stardust is open-source metering for AI and cloud usage. Wherever a token is generated or a query runs, it records what was used, what it cost, and what it cost the planet (electricity, CO₂e, water). It shows the result as a compact code such as `B2-M`, and it can route residual emissions to vetted carbon-removal providers.

A project of [AI for Ecology](https://aifore.org). See the [spec (v1.4) and summary](docs/).

## How it fits together

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="stardust-technical-architecture-dark.png">
  <img src="stardust-technical-architecture.png" alt="Stardust technical architecture: subscriber-side clients (chat plugin, browser extension, cloud and infra agent, database agent) send events through the ingestion API (REST and gRPC) to the middleware and telemetry core, which holds the Net Impact Ledger; three MCP servers (subscriber reporting, provider insights, operator lifecycle) query the core; provider drivers (direct air capture, point-source, nature-based, ocean-based) send verified telemetry through the provider driver interface.">
</picture>

```
 subscriber-client/            middleware/                     providers-service/
 ───────────────────           ──────────────────────          ────────────────────
 browser extension  ──events─▶ Stardust Core                   Remediation Provider SDK
 chat connectors               · cost (litellm pricing)        · reforestation, blue carbon,
 infra SDKs                    · energy / CO₂e / water           DAC, biochar …
                   ◀─code───── · indicator code + job ID
                               · Remediation Broker ◀─────────▶ providers (vetted, tiered)
                   ──quotes──▶   (subscriber API)
                                          │
                               schema/  (the open standard all three share)
```

| Directory | What it is | Spec |
|---|---|---|
| [`schema/`](schema/) | Event schemas, Energy Source Codes and the versioned methodology factors | §5, §7, §8, §10.5, §11.5, §20, §21.7, §22.8 |
| [`middleware/`](middleware/) | Stardust Core: ingestion, enrichment and the remediation broker (Python/FastAPI) | §8–§11, §21.7, §22.8, §23 |
| [`subscriber-client/`](subscriber-client/) | Clients that capture usage: the Chrome/Edge extension for now (TypeScript) | §6, §12, §23.3 |
| [`providers-service/`](providers-service/) | The interface remediation providers implement, a mock provider, and carbon-capture telemetry models | §10.2, §20 |
| [`mcp/`](mcp/) | MCP servers for asking Stardust questions from AI assistants: Subscriber reporting for now (Python 3.10+) | §9.5, §23 |
| [`docs/`](docs/) | The product spec and summary | |

## Status: v0.1 scaffold

This is the Phase 1 foundation (§16). The methodology factors are a **draft**, and some are placeholders awaiting review (see [`schema/README.md`](schema/README.md)). Core stores its data in a single SQLite file (see [the database design](docs/architecture/database.md)), and the extension's figures are estimates.

## Where it's going

Spec v1.2 extends Stardust beyond carbon to **five impact dimensions** (carbon, electricity, water, heat and materials), matched like for like with renewable energy certificates, water restoration certificates, heat-reuse and recycling credits through registry and market connectors. It also splits carbon into lifecycle components (inference, facility, training, embodied hardware). The architecture and the step-by-step plan for building this without disrupting what already runs are in [`docs/architecture/impact-credits.md`](docs/architecture/impact-credits.md).

Spec v1.4 adds telemetry for **when** energy is used (time-of-use tariffs and carbon-aware load shifting), embodied carbon by hardware component, and a lifecycle record for each device from deployment through reuse and recycling ([`time-embodied-lifecycle.md`](docs/architecture/time-embodied-lifecycle.md)). It also defines an **MCP interface**: three servers that let people ask Stardust questions from Claude and other AI assistants. MCP never captures telemetry and never buys or retires credits; those stay on the ingestion and REST APIs ([`mcp.md`](docs/architecture/mcp.md)).

## Quick start

```bash
python3 -m venv ~/.venvs/stardust
~/.venvs/stardust/bin/pip install -e "middleware[dev]" -e "providers-service[dev]"
~/.venvs/stardust/bin/pytest middleware providers-service
cd middleware && ~/.venvs/stardust/bin/uvicorn stardust_core.main:app --port 8080
```

Then build and load the [browser extension](subscriber-client/browser-extension/).

## License

Code is licensed under the [Apache License 2.0](LICENSE). The pricing data in `middleware/data/` comes from [BerriAI/litellm](https://github.com/BerriAI/litellm) (MIT). The Stardust and AIforE names and the indicator visual language are trademarks, handled separately from the code license (§17.3).
