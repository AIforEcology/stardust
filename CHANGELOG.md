# Changelog

Notable changes to Project Stardust. The repository holds several packages, each with its own version. Entries name the package when a change is specific to one. Three kinds of version appear here, and they change independently:

- **Spec version:** the product specification the code implements, such as 1.4 (`schema/spec-version.json`).
- **Methodology version:** the factor set used for figures, such as 0.2 (`schema/factors/`).
- **Package versions:** semver, one per package.

Versions follow [Semantic Versioning](https://semver.org/). The format follows [Keep a Changelog](https://keepachangelog.com/).

## Unreleased

### Added
- **SDKs ready for outside developers (MVP).** Both SDKs are renamed to names AIforE controls (the old names belong to others) and work against hosted Core with an API key.
  - **aifore-stardust 0.2.0** (PyPI, was `stardust-sdk` 0.1.0). The import name is still `stardust_sdk`, so code doesn't change. **Needs Python 3.10+** (0.1.0 allowed 3.9, which is past end of life); Core, providers-service and intel still support 3.9. `Stardust(api_key=...)` sends `Authorization: Bearer <key>`, falling back to `STARDUST_API_KEY`. `api_base` now defaults to `STARDUST_API_BASE`, else `https://core.aifore.org` (`None` still means OTel only). A 401 or 403 logs one warning and drops events without retrying; network errors and 5xx retry as before. The key never appears in logs, `repr` or errors. New helpers, separate from metering, that raise `StardustError`: `summary()`, `summary_daily()`, `request_quote()`, `submit_order()`, `order_status()`, with typed results (`Summary`, `DailySummary`, `Quote`, `Order`, `Certificate`). Now ships `py.typed`.
  - **@aifore/stardust 0.2.0** (npm, was `@stardust/sdk` 0.1.0): the same, as `new Stardust({ apiBase, apiKey })`, `summary()`, `summaryDaily()`, `requestQuote()`, `submitOrder()` and `orderStatus()`, with TypeScript interfaces.
  - Release workflow `release-sdks.yml`: tags `sdk-py-v*` and `sdk-js-v*` build, test and publish with trusted publishing (OIDC on PyPI, `npm publish --provenance`). No publishing tokens are stored.
  - CI tests the SDK on Python 3.10 (its floor) and 3.12; the `python (3.9)` job now covers only Core, providers-service and intel. Auto-deploy's required checks add `python (3.10)` and `e2e-sandbox`, and it rebuilds the `sandbox-provider` service when `providers-service/` changes.
  - `docs/developers.md`, the developer guide, and `scripts/e2e_sandbox.py`, an end-to-end check of both SDKs against a Core with a sandbox key. CI runs it against a local Core and the sandbox provider.
- **stardust-core 0.4.0:** keys on remediation, and sandbox mode.
  - `STARDUST_REMEDIATION_AUTH` = `off` (default), `optional` or `required`. With a key, quotes and orders need the new `remediation.write` scope (order status also accepts `reporting.read`), orders are recorded under the key's organization and user, and a key only sees its own orders. `subscriber_id` is optional with a key.
  - Sandbox keys (`POST /v1/admin/keys {..., "sandbox": true}`) and sandbox providers (`approve {"tier": ..., "sandbox": true}`). A sandbox key only gets quotes from sandbox providers, a live key only from live ones. Sandbox quotes, orders and certificates carry `"sandbox": true`, and sandbox order status says "Sandbox test order: no carbon was removed and no money moved."
  - Sandbox orders are left out of `/v1/admin/fees`. Usage sent with a sandbox key is flagged, left out of live and organization totals and OTLP export, and shown only to sandbox keys (their summaries say `"sandbox": true`). Sandbox providers aren't in `GET /v1/providers`.
  - `/healthz` adds `remediation_auth` and `providers: {live, sandbox}`.
  - Database schema v5: a `sandbox` column on `api_keys`, `events` and `orders`. Existing rows are live.
- **stardust-provider:** the mock provider is now the "Stardust Sandbox Provider (test only)", with project "Test reforestation (not real)" and certificates marked "SANDBOX — not a real removal". `Certificate` gains an optional `note`.
- **Deploy:** the `sandbox-provider` Compose service (internal only), `register-sandbox-provider.sh`, and `STARDUST_REMEDIATION_AUTH=required` in `.env.example`.
- **Hosting:** automatic deploys for core.aifore.org. `deploy/auto-deploy.sh` runs from a systemd timer every 10 minutes, deploys new commits on main once CI has passed on them, rebuilds only the services they change (after a fresh backup), checks health, and rolls back and alerts Slack on failure. `/healthz` (stardust-core) and `/ledger/healthz` (ledger) report the running commit as `"commit"`; all other health fields are unchanged. The daily `core-uptime` check now also fails when the server is more than a day behind a CI-passed change. CI shellchecks the deploy scripts.
- **Spec 1.7:** §21.10, grid flexibility telemetry. It defines how AI load curtailment capacity, response speed and compliance are measured and audited, with 19 field codes (`FEV`, `FMD`, `FDS`, `DET`, `FBL`, `BLV`, `FMT`, `LKG`, `FRB`, `FWI`, `D-FRL`, `D-FRT`, `D-FDR`, `D-FPR`, `D-FSD`, `D-FNR`, `D-FEC`, `D-FAV`, `D-FCR`), baseline rules and test-event and verification rules. New `docs/stardust-spec-v1.7.docx` and Telemetry Specification T1.6. `schema/` re-exported (218 field codes), and every package now declares spec 1.7. Proposed for public comment in RFC 0001 §6.1.
- **stardust-intel 0.1.0** (new, `intel/`): Data Center Intelligence (DCI) Phase 1 prototype, rebuilt to source its facilities from the Data Center Resource Ledger. Off by default and unpublished until the board signs off the allocation method and counsel reviews right of reply (`docs/TODO.md`).
  - `stardust-intel footprint` builds per-provider footprints from the ledger's facilities, the power plants linked to them and their on-site power observations, read from `https://core.aifore.org/ledger/v1/...` or, on the server, the ledger database. Every allocation is labelled estimated, and the caveats name each gap.
  - `stardust-intel eia-state` fetches a state's generation mix and reported CO₂ rate from the EIA Open Data API v2; tested against recorded responses.
  - `stardust-intel validate` runs in CI.
- **stardust-core:** optional DCI footprint overlay, `STARDUST_FOOTPRINT_FILE`. It adds an `fp:<provider>` grid for events with no region, tags those events `estimated`, and suffixes the methodology version (`0.2.0+dci-0.1.0`). Core refuses a footprint that points a provider at a grid it doesn't define. Unset, Core's responses are byte-for-byte unchanged.
- **stardust-mcp 0.1.0** (new, `mcp/`, Python 3.10+): the Subscriber reporting MCP server (spec §23.3).
  - Four read-only tools: `get_usage_summary`, `get_impact_summary`, `explain_metric`, `quote_credits` (carbon only, quote only).
  - Authenticated with Core API keys as bearer tokens. Every answer is limited to the key's organization and user.
  - Built on the official MCP Python SDK 2.x. Has its own CI job on Python 3.12.
- **stardust-core:** `GET /v1/auth/principal` returns the organization, user and scopes a key grants. It's refused while subscriber auth is off.
- Conformance: MCP servers built so far are checked against `schema/mcp-tools.json`, and tools not built yet are reported as a warning.
- **stardust-core 0.3.0:** subscriber API keys and tenant isolation (spec §23.6, MCP plan step M1). Off by default.
  - `STARDUST_SUBSCRIBER_AUTH` = `off`, `optional` or `required`.
  - Keys belong to an organization, and optionally one user in it, and carry §23.6 scopes.
  - With a key, `/v1/summary`, `/v1/summary/daily` and `GET /v1/events` are pinned to that organization and user. Asking for another returns `403 forbidden_scope`.
  - Admin endpoints: `POST /v1/admin/keys`, `GET /v1/admin/keys`, `POST /v1/admin/keys/{key_id}/revoke`.
  - `/healthz` reports `subscriber_auth` and counts `api_keys` rows.
- Database schema v4: the `api_keys` table, which stores only key hashes.
- **Browser extension:** the popup shows the same measures as the AIforE Token Cost Calculator. "Heat released" joins the main list for the last reply and the total. A "More detail" toggle holds the rest: what the indicator code means, water split into data-center cooling and power generation, the same electricity's CO₂ from natural gas, coal or oil (U.S. EIA 2023), and the young-forest area that would absorb the CO₂e (Gray, Whittier & Harmon 2016). Uses fields Core already returns; no Core change.

### Unchanged
- With the default `off`, every endpoint behaves exactly as before.
- Ingestion and remediation endpoints don't use keys yet. (Superseded: both can now require keys, `STARDUST_INGEST_AUTH` and `STARDUST_REMEDIATION_AUTH`.)

## spec-v1.4 (tag)

### Added
- Spec v1.4, the Telemetry Specification T1.2, and `docs/tools/stardust_telemetry_sync.py` for keeping them in sync.
- Generated `schema/spec-version.json`, `field-codes.json` and `mcp-tools.json`, exported from the spec.
- Architecture docs for the MCP interface (`docs/architecture/mcp.md`) and for time-of-use, embodied carbon and the hardware lifecycle (`docs/architecture/time-embodied-lifecycle.md`).
- Planned OpenTelemetry mappings (§11.5) in `schema/otel-attributes.md`.
- **stardust-core 0.2.0:**
  - `GET /healthz` reports `spec_version`.
  - Exported spans and metrics carry the resource attribute `stardust.spec.version`.
- Every package declares the spec version it implements: `[tool.stardust] spec_version` in `pyproject.toml`, `"stardust": {"specVersion"}` in `package.json`.
- Conformance tests (`middleware/tests/test_conformance.py`):
  - Provider telemetry fields must match the spec's names and units.
  - Every package must declare the current spec version.
  - A coverage report of the spec's field codes not implemented yet (a warning, not a failure).
  - An MCP tool-set check, skipped until the servers exist.

### Changed
- **stardust-core:** the FastAPI app version now comes from the package version.
- `middleware/tests/test_schema.py` loads only files that declare `$schema`, so the generated data files aren't treated as JSON Schemas.

### Unchanged
- Methodology stays at v0.2. No figures change.
- The API responses, event schema and database schema, apart from the added `spec_version` field in `/healthz`.

## Before this changelog

Merged pull requests up to spec v1.2, all at package version 0.1.0:

- v0.1 scaffold: schema, Core, the Remediation Provider SDK and the browser extension (#1)
- OpenTelemetry receiver and exporter (#4); gRPC receiver and metrics (#5)
- Claude support in the browser extension (#6, #7)
- SQLite store (#8)
- macOS service install (#9)
- Broker fees (#10), renamed the tech operations fee (#12)
- Spec v1.2 docs (#11)
- Water split and heat telemetry, methodology 0.2 (#13)
- Technical architecture diagram (#14)
