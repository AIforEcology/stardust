# Docs

## Specification

- [`stardust-spec-v1.6.docx`](stardust-spec-v1.6.docx): **current.** *AIForE Sustainable Ecosystems SDK with Telemetry for Big Data*, product specification v1.6 (draft).
  - New in v1.6 (merged from Telemetry Specification T1.4 and the T1.5 time patch):
    - §7, interval, factor-resolution, price, demand-charge and matching fields
    - §21.7, the time dimension: fields `IVL`, `FTR`, `FVW`, `FBS`, `DMD`, `PKD`, `HMS`, `D-DCC` and `D-TMC`, and a new §21.7.4 on temporal resolution and matching rules
    - §21.8, energy-aware routing telemetry and method
    - §21.9, accelerator, node and facility runtime telemetry
- [`stardust-spec-v1.4.docx`](stardust-spec-v1.4.docx): the previous version, kept for reference.
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
- [`stardust-telemetry-spec-T1.5.docx`](stardust-telemetry-spec-T1.5.docx): the Telemetry Specification, a subset of v1.6 holding only the telemetry sections (now including §21.8 and §21.9), for editing on its own. Edits merge back into the master with [`tools/stardust_telemetry_sync.py`](tools/stardust_telemetry_sync.py).
- [`stardust-telemetry-spec-T1.2.docx`](stardust-telemetry-spec-T1.2.docx): the earlier Telemetry Specification (a subset of v1.4), kept for reference.
- [`stardust-summary.pdf`](stardust-summary.pdf): four-page project summary with the data-flow, marketplace and right-sizing diagrams (written for v0.9).

Section references in the code (like `§8.4`) point to the spec. Every version since v0.9 has kept earlier section numbers and only inserted new sections, so older references are still correct.

### Machine-readable spec files

`tools/stardust_telemetry_sync.py export` writes three files into [`schema/`](../schema/): the spec version, every field code and every MCP tool. They're generated from the spec, so change the spec and re-export rather than editing them (see [`schema/README.md`](../schema/README.md)).

## RFCs

- [`rfc/`](rfc/): proposed open standards, published for public comment. [RFC 0001](rfc/0001-unified-telemetry.md), the unified telemetry specification, is open for 60 days of comment.

## Architecture

- [`architecture/database.md`](architecture/database.md): how Stardust Core stores data. It covers why SQLite, the schema, write and read paths, durability, privacy controls, performance, operations, migrations and the path to PostgreSQL.
- [`architecture/impact-credits.md`](architecture/impact-credits.md): the v1.2 extension from carbon to five impact dimensions (carbon, electricity, water, heat, materials) and lifecycle emissions. It covers the target architecture, how it maps onto today's code, and the incremental, non-destructive build plan. Its ground rules apply to all the plans below.
- [`architecture/time-embodied-lifecycle.md`](architecture/time-embodied-lifecycle.md): the v1.3 telemetry for time-of-use and load shifting, component-level embodied carbon, and the hardware lifecycle record.
- [`architecture/mcp.md`](architecture/mcp.md): the v1.4 MCP interface. It maps each tool to today's Core endpoints and plans the three servers.
- [`tools/stardust_architecture_diagram.py`](tools/stardust_architecture_diagram.py): draws the README's architecture diagrams, including the proposed subscriber EAC credit path. It needs matplotlib. Run `python docs/tools/stardust_architecture_diagram.py light` and `… dark` from the repo root to regenerate `stardust-technical-architecture.png` and `stardust-technical-architecture-dark.png`.

## Operations

- [`TODO.md`](TODO.md): open work, and the recurring checklists, including the **weekly tech operations fee review**.
