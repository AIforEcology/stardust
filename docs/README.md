# Docs

## Specification

- [`stardust-spec-v1.2.docx`](stardust-spec-v1.2.docx): **current.** *AIForE Sustainable Ecosystems SDK with Telemetry for Big Data*, product specification v1.2 (draft). New in v1.2:
  - §8.8, lifecycle emissions: training, inference, facility and embodied hardware
  - §22, multi-attribute impact credits: renewable energy, water, heat recovery and materials, with registry and market connectors and the Net Impact Ledger
  - Phase 5 on the roadmap, and new open questions in §19
- [`stardust-spec-v0.9.docx`](stardust-spec-v0.9.docx): the earlier draft, kept for reference.
- [`stardust-summary.pdf`](stardust-summary.pdf): four-page project summary with the data-flow, marketplace and right-sizing diagrams (written for v0.9).

Section references in the code (like `§8.4`) point to the spec. v1.2 kept every earlier section number and only inserted §8.8 and §22, so references written against v0.9 are still correct.

## Architecture

- [`architecture/database.md`](architecture/database.md): how Stardust Core stores data. It covers why SQLite, the schema, write and read paths, durability, privacy controls, performance, operations, migrations and the path to PostgreSQL.
- [`architecture/impact-credits.md`](architecture/impact-credits.md): the v1.2 extension from carbon to five impact dimensions (carbon, electricity, water, heat, materials) and lifecycle emissions. It covers the target architecture, how it maps onto today's code, and the incremental, non-destructive build plan.
