# MCP interface

The architecture for spec v1.4 §9.5 and §23: what the Model Context Protocol interface adds to Stardust, how each tool maps onto today's Core, and the incremental plan for building it.

**Status:** step M1 (subscriber keys and tenant isolation in Core) is built; no MCP server exists yet. The tool list lives in [`schema/mcp-tools.json`](../../schema/mcp-tools.json), generated from the spec, and a conformance test will check the servers against it once they exist.

## What v1.4 adds

### One rule for every integration (§9.5)

**Data comes in through APIs; questions and light updates go through MCP.** MCP tools run only when a model calls them, so they can't serve as a metering feed, and they never move money or credits.

| Path | Protocol |
|---|---|
| Subscriber telemetry | REST and OTLP ingestion (`POST /v1/events`, `POST /v1/traces`, gRPC) |
| Provider telemetry | Provider SDK over the ingestion API (§10.2) |
| Registries and markets | Registry and Market Connector adapters (§22.5) |
| Credit purchases and retirements | Dashboard and REST API **only** |
| Conversational reporting | MCP: Subscriber reporting and Provider insights servers |
| Operator lifecycle updates | MCP: Operator lifecycle server, with propose and confirm. Bulk imports use the API |

### Three servers (§23.2)

| Server | Audience | Access | OAuth scopes | Distribution |
|---|---|---|---|---|
| Subscriber reporting (§23.3) | Individuals, teams, org admins | Read-only | `reporting.read` | Eligible for connector directories, such as Claude's |
| Provider insights (§23.4) | Registered Providers | Read-only | `provider.read` | Partner distribution |
| Operator lifecycle (§23.5) | AI infrastructure operators | Read plus confirmed writes | `operator.lifecycle.read`, `operator.lifecycle.write` | Private or enterprise only. **Never directory-listed** |

The rules that shape the design (§23.1, §23.5–§23.8):

- **Same numbers everywhere.** Results use the spec's field codes and OTel names, so an answer in chat matches the dashboard.
- **Write tools exist only on the operator server** (§12.2, §22.8.2). The reporting and Provider servers are read-only, with every tool annotated `readOnlyHint: true`.
- **No transactions.** `quote_credits` returns quotes and a link to act on them; it never buys.
- **Two-step writes.** `propose_lifecycle_update` writes nothing and returns a `change_id` and a diff. `confirm_lifecycle_update` applies it after the user approves, through MCP elicitation where the client supports it.
- **Privacy.** No tool returns prompt or response content (§14.1).
- **Tenant isolation** is enforced server-side on every call, and every write is audit-logged with actor, time, tool, `change_id` and before and after values.
- **Response conventions.** Each result has structured content validated against an output schema, plus a short plain-language summary. Every value carries its unit, field code, period, confidence tag, the spec version, and a link to the matching dashboard view.
- **Errors** use standard MCP error responses with Stardust reason codes: `unauthorized`, `forbidden_scope`, `not_found`, `invalid_period`, `rate_limited`, and a `stale_data` warning.
- **Versioning.** Tool names are stable. A breaking change ships as a new tool (`get_usage_summary_v2`), and the old one stays for at least six months.

## How it maps onto today's code

### Subscriber reporting (§23.3)

| Tool | Today | Gap |
|---|---|---|
| `get_usage_summary` | `GET /v1/summary` and `/v1/summary/daily`: tokens, cost, events, by user or org and period | `group_by` model, provider or project; a request count distinct from events |
| `get_impact_summary` | `GET /v1/summary`: energy, operational CO₂e (= `OPE`), water on-site and off-site, heat | `FAC`, `TRA`, `EMB` (impact-credits step 2); per-value confidence tags in the aggregate |
| `get_right_size_report` | Right-sizing weights exist in the methodology file; no scores computed | All of §21.3 (`D-RSS` and sub-scores, efficiency indexes) |
| `list_recommendations` | None | §21.5 recommendations, `D-SAV`, `D-LXS` |
| `get_load_shift_report` | None | All of §21.7 ([time-embodied-lifecycle.md](time-embodied-lifecycle.md)) |
| `get_net_impact_ledger` | None | `GET /v1/ledger` (impact-credits step 3) |
| `quote_credits` | `POST /v1/remediation/quotes`, carbon only | Impact-vector baskets (impact-credits step 5). Carbon quotes can be served now |
| `explain_metric` | `schema/field-codes.json` holds definition, unit and section for every code; `GET /v1/methodology` holds the factors | None: can be built from the generated file |

### Provider insights (§23.4)

| Tool | Today | Gap |
|---|---|---|
| `get_demand_forecast` | None | Aggregate demand by `credit_category` and region |
| `list_orders` | Orders are stored, but only fetched one at a time | An endpoint listing a Provider's own orders by status and period |
| `get_order_status` | `GET /v1/remediation/orders/{order_id}` | `serials` and `retirement_ref` (impact-credits step 5) |
| `get_issuance_status` | None | `get_issuance()` on the Registry and Market Connector (impact-credits step 6) |
| `get_telemetry_health` | Providers accept §20 telemetry (`POST /telemetry` in the Provider SDK) | Gap detection and the derived `ADF` and `DQS` in Core |

### Operator lifecycle (§23.5)

None of these exist. They need the §22.8 asset store first ([time-embodied-lifecycle.md](time-embodied-lifecycle.md), step L1): `list_assets`, `get_asset_record`, `propose_lifecycle_update`, `confirm_lifecycle_update`, `request_recycler_confirmation`.

### Cross-cutting gaps

- **Authentication.** Built in M1: subscriber API keys that carry the four §23.6 scopes, and tenant isolation on Core's read endpoints (`STARDUST_SUBSCRIBER_AUTH`, off by default; see the [middleware README](../../middleware/README.md#subscriber-api-keys)). What remains is **OAuth for the MCP servers** (M2):
  - The servers validate the user's OAuth token.
  - They call Core with the same principal: organization, optional user and scopes.
  - Core already enforces isolation for that principal, so an MCP server can't widen it.
  - Ingestion still takes calls without a key.
- **Spec version in responses.** Core already reports it (`GET /healthz` → `spec_version`, and the `stardust.spec.version` OTel resource attribute). The servers can pass it through.
- **Audit log** for operator writes: a new table.

## Where the servers live

**Decided (September 2026):** a new top-level `mcp/` package (Python, like Core), with one entry point per server, that calls Core over its REST API rather than importing Core.

- **Keeps Core free of write tools.** The reporting and Provider servers can only do what Core's read endpoints allow.
- **Keeps each server's surface small**, which directory review needs (§12.2), and lets the operator server ship separately.
- **Deploys independently.** Core can be self-hosted (§9.3) with or without MCP.
- **Matches the repo's layout**: one directory per component.

The alternative, routes inside Core, is less code but mixes MCP's OAuth with Core's ingestion. It also makes it harder to show that the reporting server has no write paths.

The package is created in M2. Adding an MCP SDK dependency still needs approval.

## Incremental plan

The [ground rules in impact-credits.md](impact-credits.md#ground-rules-for-every-step) apply: additive only, migrations add, new behavior behind a switch, one small PR per step with docs.

| # | Step | What changes | What stays untouched |
|---|---|---|---|
| M0 | **Docs and conformance** (spec v1.4 update) | This doc; `schema/mcp-tools.json`; a tool-set conformance test, skipped until servers exist | All code |
| M1 | **Subscriber auth in Core** (built) | API keys with §23.6 scopes; reads pinned to the key's organization and user; `401 unauthorized` and `403 forbidden_scope` reason codes. `STARDUST_SUBSCRIBER_AUTH=off` (default), `optional` or `required` | Every endpoint while the switch is `off`; ingestion and remediation in any mode |
| M2 | **Reporting server, first tools** | `mcp/` package; OAuth, mapped to Core principals; `get_usage_summary`, `get_impact_summary` (operational only, with the gap disclosed), `explain_metric`, `quote_credits` (carbon only). All read-only | Core |
| M3 | **Provider insights, first tools** | `list_orders` (needs a new read endpoint), `get_order_status` | Broker, orders |
| M4 | **Remaining reporting tools**, one per PR as Core gains the data | `get_net_impact_ledger` after ledger step 3; right-size and recommendations after §21 scoring; `get_load_shift_report` after §21.7 | Earlier tools |
| M5 | **Operator lifecycle server** | Separate entry point and distribution; propose and confirm; audit log. After the asset store (L1) | Reporting and Provider servers |
| M6 | **Directory submission** | Submit the reporting server to connector directories | |

## Spec wording to reconcile

- §22.5 still says the broker "purchases from Provider inventory or a market". Under the [operating model](impact-credits.md#operating-model-technology-and-connection-not-financial-brokerage), AIforE never takes title, so it should read that **the Subscriber purchases from the Provider**. `quote_credits` returns a §22.5 basket, so the fix matters for the tool's description too.
