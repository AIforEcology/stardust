# Changelog

Notable changes to Project Stardust. The repository holds several packages, each with its own version. Entries name the package when a change is specific to one. Three kinds of version appear here, and they change independently:

- **Spec version:** the product specification the code implements, such as 1.4 (`schema/spec-version.json`).
- **Methodology version:** the factor set used for figures, such as 0.2 (`schema/factors/`).
- **Package versions:** semver, one per package.

Versions follow [Semantic Versioning](https://semver.org/). The format follows [Keep a Changelog](https://keepachangelog.com/).

## Unreleased: spec v1.4

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
