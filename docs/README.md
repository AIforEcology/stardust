# Docs

## Specification

- [`stardust-spec-v1.4.docx`](stardust-spec-v1.4.docx): **current.** *AIForE Sustainable Ecosystems SDK with Telemetry for Big Data*, product specification v1.4 (draft).
  - New in v1.3:
    - §8.8.8, component-level embodied carbon and hardware refresh cycles
    - §11.5, harmonization rules: reuse OpenTelemetry `hw.*` and `host.*` and DMTF Redfish first, and propose vendor-neutral names upstream
    - §21.7, time-of-use tariffs and carbon-aware load shifting
    - §22.8, a hardware lifecycle record for reuse and recycling
  - New in v1.4:
    - §9.5, integration protocols: data comes in through APIs; questions and light updates go through MCP
    - §23, the MCP interface: Subscriber reporting, Provider insights and Operator lifecycle servers, with their tools, OAuth scopes and response conventions
    - §12.2 and §22.8.2 now say that write tools exist only on the operator server
- [`stardust-spec-v1.2.docx`](stardust-spec-v1.2.docx): the previous version, kept for reference.
- [`stardust-telemetry-spec-T1.2.docx`](stardust-telemetry-spec-T1.2.docx): the Telemetry Specification, a subset of v1.4 holding only the telemetry sections, for editing on its own. Edits merge back into the master with [`tools/stardust_telemetry_sync.py`](tools/stardust_telemetry_sync.py).
- [`stardust-summary.pdf`](stardust-summary.pdf): four-page project summary with the data-flow, marketplace and right-sizing diagrams (written for v0.9).

Section references in the code (like `§8.4`) point to the spec. Every version since v0.9 has kept earlier section numbers and only inserted new sections, so older references are still correct.

### Machine-readable spec files

`tools/stardust_telemetry_sync.py export` writes three files into [`schema/`](../schema/): the spec version, every field code and every MCP tool. They're generated from the spec, so change the spec and re-export rather than editing them (see [`schema/README.md`](../schema/README.md)).

## Architecture

- [`architecture/database.md`](architecture/database.md): how Stardust Core stores data. It covers why SQLite, the schema, write and read paths, durability, privacy controls, performance, operations, migrations and the path to PostgreSQL.
- [`architecture/impact-credits.md`](architecture/impact-credits.md): the v1.2 extension from carbon to five impact dimensions (carbon, electricity, water, heat, materials) and lifecycle emissions. It covers the target architecture, how it maps onto today's code, and the incremental, non-destructive build plan. Its ground rules apply to all the plans below.
- [`architecture/time-embodied-lifecycle.md`](architecture/time-embodied-lifecycle.md): the v1.3 telemetry for time-of-use and load shifting, component-level embodied carbon, and the hardware lifecycle record.
- [`architecture/mcp.md`](architecture/mcp.md): the v1.4 MCP interface. It maps each tool to today's Core endpoints and plans the three servers.

## Operations

- [`TODO.md`](TODO.md): open work, and the recurring checklists, including the **weekly tech operations fee review**.
