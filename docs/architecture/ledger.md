# Data Center Resource Ledger: database and API

The Data Center Resource Ledger is AIforE's open, source-cited record of every known data center and the power plants built for them. It covers power sources, water sources and use, emissions, permits, subsidies and claims, and every fact carries its source, date, method and confidence. It began as a Google Sheet (about 3,600 US sites), kept up to date by seven scheduled Claude agents and a public corrections page. This document describes the database that replaces the sheet's storage.

Code: [`ledger/`](../../ledger/). Hosting: [`deploy/`](../../deploy/README.md#data-center-resource-ledger).

## Design

```
                internet
                   │ https://core.aifore.org
                ┌──▼───┐
                │Caddy │  /v1/admin/*  and  /ledger/v1/admin/*  → 404
                └┬────┬┘
   everything else│    │/ledger/*  (prefix stripped)
            ┌─────▼┐  ┌▼────────┐      ┌───────────┐
            │ core │  │ ledger  │─────▶│ ledger-db │  PostgreSQL 17 + PostGIS 3
            │SQLite│  │ FastAPI │      │ (no ports)│  shared_buffers 256MB, 30 connections, 1.5 GB limit
            └──────┘  └─────────┘      └─────▲─────┘
                                             │ pg_dump -Fc nightly
                                       ┌─────┴───────┐
                                       │ledger-backup│ → deploy/backups/ledger-YYYYMMDD.dump (14 kept)
                                       └─────────────┘   → Object Storage (offsite-backup.sh)
```

- **Separate from Stardust Core.** The ledger has its own process, database, volume and backups. Core's code, SQLite database and routes are unchanged, and Core neither reads nor depends on the ledger. If the ledger or its database is down, Core keeps serving the browser extension.
- **Small.** On the 1 OCPU / 6 GB Always Free host, `ledger-db` is capped at 1.5 GB (`shared_buffers=256MB`, `max_connections=30`), `ledger` at 384 MB and `ledger-backup` at 256 MB. Core uses about 100 MB.
- **arm64.** `postgis/postgis:17-3.5` publishes amd64 only (checked 2026-11-03), so [`deploy/ledger-db.Dockerfile`](../../deploy/ledger-db.Dockerfile) adds `postgresql-17-postgis-3` to the multi-arch `postgres:17-bookworm` image. CI runs on amd64 and tests against `postgis/postgis:17-3.5`; it also builds the production Dockerfile.
- **Plain SQL.** psycopg 3, no ORM. The schema is a set of numbered files in [`ledger/migrations/`](../../ledger/migrations/), applied in order at startup in a transaction each, and recorded in `schema_migrations`. An advisory lock makes concurrent starts safe. To change the schema, add `002_<name>.sql`; never edit an applied file.

## Rules, and where they are enforced

| Rule | Enforced by |
|---|---|
| **IDs never change.** `DC-US-TX-0152`, `PWR-US-OH-0001`; counted per (prefix, country, state) | `id_counters` and `ledger_next_number()`: an UPSERT that row-locks the counter inside the write's transaction, so concurrent writers queue, and a rolled-back write gives its number back (no duplicates, no gaps). The ID format is a `CHECK` on the key column; there is no route that changes a key, and foreign keys are `ON UPDATE RESTRICT`. A Master List ID merged into a curated facility lives on in `facility_aliases`: it still resolves and is never reused. Observations, claims, who-pays items and permit checks use the same counters (`OBS-0001`, `CLM-0001`, `WP-0001`, `PVA-0001`). |
| **Provenance on every fact.** | `observations` and every tracker table have `source_url`, `source_title`, `source_type`, `source_date`, `method`, `confidence` (`CHECK 0–1`), `review_status` and `date_found`. Observations require a source URL. |
| **Append-only facts.** | The `observations_append_only` trigger refuses every `DELETE`, and every `UPDATE` outside an admin transaction; an admin may change only `review_status` and `notes`. The API has no update or delete route for observations (405). A correction is a new row whose `supersedes` points at the old one; the facility view shows only the latest, unsuperseded, non-rejected observation for each field. |
| **Reviewed rows are protected.** | The `ledger_protect_reviewed` trigger refuses any insert, update or delete that touches a `Reviewed` or `Published` row unless the transaction is an admin one. The API checks first, to give a clear 409. |
| **Every change is audited.** | The `ledger_audit` trigger writes every update and delete to `audit_log`: key ID, action, table, row, before and after (JSON), and the source given for the change. The API runs every write under the caller's key ID (`SET LOCAL ledger.key_id`), so agent updates are audited just like admin ones. Key creation, revocation and each import batch are logged too. A changed claim verdict also adds a `claim_log` row. |
| **Unknown is not zero.** | Numbers are nullable and empty cells stay NULL. "Not quantified" is NULL. Generated scores are NULL when an input is unscored. |
| **Agents fill blanks, and re-assess only what they own.** | `store._fill`: any write key may fill blank fields. Agents may also overwrite the fields listed for each table in `models.AGENT_REFRESH` (next section), on rows that are not Reviewed or Published, if they give a source for the change. Overwriting anything else returns 409 `field_not_refreshable` with the field names; only `/v1/admin/rows/...` may. A blank value never erases a filled one. |
| **Idempotent writes.** | Every writable table has a unique `idempotency_key`. A retry with the same key returns the first row (200, `"idempotent_replay": true`). Observations without a key are also matched on their natural key (facility, field, value, source URL). An advisory lock on the key closes the race between two retries. |
| **No personal contact details in corrections.** | `corrections` has no column for them, `submitter_role` may not contain `@`, and the importer drops (and never reads) any column it doesn't know. |

An "admin transaction" is one the API opens for an admin caller: it runs `SET LOCAL ledger.admin = 'on'` and `ledger.key_id = <key>`, which last only until that transaction ends.

## Schema

Columns keep the sheet's meanings, because the agents' prompts describe those exact fields. The sheet column each one comes from is in the comments in [`001_init.sql`](../../ledger/migrations/001_init.sql) and in the importer's column maps ([`importer.py`](../../ledger/src/ledger/importer.py)).

| Table | Sheet tab | Key | Notes |
|---|---|---|---|
| `facilities` | Facilities + US Master List | `facility_id` | `geom geography(Point,4326)` (GiST index), `location_precision`, `coordinate_source`, external IDs `compute_atlas_id`, `peeringdb_ids text[]`, `epoch_name`, `epa_frs_id`, `eia_plant_ids text[]`, `pwsid`, `parcel_id`, `county_fips char(5)` (each indexed), `sources text[]`, MW figures, `review_status`, `notes`, `first_seen`. `state` is ISO 3166-2 (`US-TX`). |
| `facility_aliases` | US Master List | `alias_id` | Master List IDs that were matched to a curated facility ("Curated Facilities ID"). |
| `facility_names` | Facilities: Other names | `id` | Alternative names, code names, LLCs, with source. Unique per facility. |
| `facility_links` | Facilities: Serves / served by | `id` | `from_facility_id` serves `to_facility_id`; `link_status` is `confirmed` or `unconfirmed`, plus `confidence`. |
| `observations` | Observations (A–T) | `obs_id` | `field_group` is one of the nine: Status & size; Power: grid; Power: sources; Power: on-site; Water: supply; Water: use & discharge; Emissions & air; Mitigation; Economics & community. Plus `supersedes`. |
| `claims`, `claim_log` | Claims, Claim log | `claim_id`, `id` | `verdict` is one of the five: Verified; Consistent; Partly verified / missing context; Unverified; Contradicted by record. "Claim source URL/title" are `source_url`/`source_title`. Evidence observation IDs are a `text[]` (ranges like `OBS-0028 to OBS-0031` are expanded on import). |
| `permit_checks` | Permitted vs actual | `check_id` | `gap` = `actual_value − limit_value`, generated. "Actual source"/"Actual method" are `source_*`/`method`. |
| `water_ledger` | Water ledger | `facility_id` | `completeness` (0–6), generated: cooling type known, supply type known, supplier named, a cap or projection, a reported actual, discharge details. |
| `who_pays` | Who pays | `item_id` | `value_usd numeric`, NULL when not quantified. |
| `load_reality` | Load reality | `facility_id` | Six factor scores (0–2); `deliverability_score` = round(sum / 12 × 100), `tier` and `score_weighted_mw` generated, all NULL if any factor is unscored. |
| `market_context` | Load reality (Market context block) | `id` | `value` is text (some values are pairs, e.g. `331 / 195`). |
| `corrections` | Corrections | `correction_id` | No personal contact details. Written only by admin. |
| `source_registry` | Sources & credits | `source` | Licence and required credit line. |
| `run_log` | Run log | `id` | One row per agent run, plus `agent`. |
| `api_keys` | — | `key_id` | `key_hash` (SHA-256) only, `scopes text[]`, `label`, `created_at`, `revoked_at`. Same pattern as Core's keys. |
| `audit_log` | — | `id` | Who (key ID), what, before, after, when. |

Review statuses in use: `Needs review`, `Imported, not reviewed`, `Verified`, `Rejected`, `Conflict`, `Reviewed`, `Published`. Only the last two are protected.

## API

Base URL: `https://core.aifore.org/ledger`. The OpenAPI schema is at `/ledger/openapi.json`. (The interactive docs page is off: behind the stripped prefix it would load Core's schema.)

### Public reads (no key; 120 requests per minute per IP)

| Endpoint | Filters |
|---|---|
| `GET /healthz` | |
| `GET /v1/facilities` | `state` (`TX` or `US-TX`), `type`, `status`, `operator` (contains), `q` (name, other names, operator, tenant or ID), `near=lat,lon&km=` (default 25), `bbox=min_lon,min_lat,max_lon,max_lat`, `format=geojson`, `limit` (≤1000), `offset` |
| `GET /v1/facilities/{id}` | With `names`, `links`, `aliases` and `latest_observations`. Master List aliases resolve. |
| `GET /v1/observations` | `facility_id`, `field_group`, `since` (date found) |
| `GET /v1/claims`, `/v1/claims/{id}` | `facility_id`, `verdict`, `claim_type`; the detail includes the claim log |
| `GET /v1/water`, `/v1/who-pays`, `/v1/load-reality`, `/v1/permit-checks` | `facility_id`, and `state` / `category` / `tier` / `status` respectively; load reality includes `market_context` |
| `GET /v1/sources` | Licences and credits |

Lists return `{items, total, limit, offset, next_offset, attribution}`. Every response carries the `attribution` block: CC BY 4.0 for Compute Atlas and Epoch AI, the PeeringDB credit, and the ODbL note for when OpenStreetMap data is added.

### Writes (`X-Ledger-Key: lk_<key_id>_<secret>`)

| Scope | Endpoints |
|---|---|
| `observations:write` | `POST /v1/observations` (append-only) |
| `facilities:write` | `POST /v1/facilities` (send `id_prefix` `DC` or `PWR`, `state`, `country`; the server assigns the ID), `PATCH /v1/facilities/{id}` (fill blanks; re-assess the fields agents may update), `POST /v1/facilities/{id}/names`, `POST /v1/facilities/{id}/links` |
| `tracker:write` | `POST /v1/claims`, `PATCH /v1/claims/{id}`, `POST /v1/claims/{id}/log`; `POST /v1/water` and `POST /v1/load-reality` (one row per facility: create it, or update it); `POST`/`PATCH /v1/who-pays`, `/v1/permit-checks`, `/v1/market-context` |
| `runlog:write` | `POST /v1/run-log` |
| `admin` | `/v1/admin/*` only (below) |

Send an `idempotency_key` with every write (for example `<agent>-<run date>-<n>`). Responses: 201 created; 200 with `"idempotent_replay": true` for a retry; 401 missing, invalid or revoked key (`reason: key_revoked`); 403 wrong scope; 409 `field_not_refreshable`, `row_protected`, `protected_status` or `append_only`, with the reason in `detail`; 422 invalid value, with the `field` (`change_source_url` when an overwrite has no source).

Example:

```bash
curl -s https://core.aifore.org/ledger/v1/observations -H "X-Ledger-Key: $LEDGER_KEY" -H 'Content-Type: application/json' -d '{
  "idempotency_key": "daily-scan-2026-11-03-7",
  "facility_id": "DC-US-TX-0152", "field_group": "Power: on-site", "field": "Gas turbines",
  "value": "12", "unit": "units", "source_title": "TCEQ air permit 123456", "source_url": "https://...",
  "source_type": "Permit", "source_date": "2026-10-30", "method": "Read from permit", "confidence": 0.9
}'
```

### Admin (server only)

Blocked by Caddy (404 from the internet) and, as a second guard, refused by the app for any request that came through a proxy. Authenticate with `X-Ledger-Admin-Token: $LEDGER_ADMIN_TOKEN` or a key with the `admin` scope.

| Endpoint | |
|---|---|
| `POST /v1/admin/keys` | `{"scopes": [...], "label": "..."}`; the key is shown once |
| `GET /v1/admin/keys`, `POST /v1/admin/keys/{key_id}/revoke` | |
| `PATCH /v1/admin/rows/{table}/{id}` | Overwrite any field, including on Reviewed/Published rows (observations: `review_status` and `notes` only). Audited. |
| `POST /v1/admin/import` | `{"table", "mode": "upsert" or "fill", "rows"}`; used by the import script |

## Import from the sheet

[`ledger/scripts/import_sheet.py`](../../ledger/scripts/import_sheet.py) reads one CSV per tab (File → Download → CSV in Google Sheets) and loads them in order. It keeps every ID, upserts by ID (so re-running changes nothing), and writes `import-report.md` and `.json`: rows loaded, rows rejected and why, duplicate IDs, coordinates outside the US, bad confidence values (left blank), and columns it didn't import. Curated Facilities rows load first; Master List rows then add new sites, or fill blank fields of the curated facility they point at, whose Master List ID becomes an alias. A blank cell never erases a value. **Never commit the CSVs**: the repository is public and the sheet holds unreviewed notes (`.gitignore` excludes them).

## Operations

Deploying, updating, backups, restoring and rotating keys are in [`deploy/README.md`](../../deploy/README.md#data-center-resource-ledger).

## What agents can do

Agents should be able to keep doing everything they do in the sheet today. Each agent's key has one scope. Every run it can:

- **Add facts.** It adds observations (append-only). A correction is a new observation with `supersedes`.
- **Add rows.** It adds facilities (the server assigns the ID), names, links, claims, claim-log entries, who-pays items, permit checks, market context and run-log rows.
- **Fill blanks** on any row that isn't Reviewed or Published.
- **Re-assess** the fields below on rows that aren't Reviewed or Published. One list in the code, [`models.AGENT_REFRESH`](../../ledger/src/ledger/models.py), defines them:

| Table | Fields agents may overwrite | Fixed (fill-only) |
|---|---|---|
| `claims` | verdict, reasoning, record_checked, evidence_supporting, evidence_against, evidence_observation_ids, operator_response, confidence, next_step, last_checked, review_status | The claim itself: claim_text, claimant, claim_type, claim_date, its source; facility |
| `water_ledger` | Every water-profile field (cooling, supply, supplier, PWSID, allocation, projected and actual use, withdrawal vs consumption, discharge, reclaimed water, drought terms, wells, basin stress), key_sources, evidence_observation_ids, next_step, last_checked, review_status, source fields | facility_id, facility_label, state |
| `who_pays` | description, value_usd, value_basis, term, conditions, evidence_observation_ids, next_step, last_checked, review_status, source fields | The item: facility, category, who pays, counterparty, year |
| `load_reality` | stated and operating load (and their basis and source), status, the six factor scores, evidence_for_scores, red_flags, sources, next_step, last_checked, review_status, source fields | facility_id, facility_label, state |
| `permit_checks` | actual_value, actual_as_of, status, enforcement_disputes, evidence_observation_ids, next_step, last_checked, review_status, source fields (the actual's source) | The limit: metric, limit_value, unit, limit_type, limit source; facility. A new limit is a new check. |
| `market_context` | value, unit, as_of, note, review_status, source fields | measure |
| `facilities` | status, operator, tenant, latitude/longitude, location_precision, coordinate_source, utility, grid_region, operational_mw, planned_mw, review_status | Identity: name, state, country, county, FIPS; external IDs; first_seen; notes. Other names go to `facility_names`. |

"Source fields" are source_url, source_title, source_type, source_date, method, confidence and date_found.

Every overwrite needs a source:
- `change_source_url` in the request, or the row's new `source_url` (the facility's new `coordinate_source` for coordinates);
- for a claim, evidence observation IDs, which must exist and each carry their own source.

Overwriting only `last_checked`, `next_step` or `review_status` needs no source. Each overwrite is audited with the key ID, before, after and source, and a changed verdict also writes a `claim_log` row. A retry with the same values changes nothing and logs nothing.

**Facilities: why a scoped update, plus observations.** In the sheet, agents record every new fact as an observation and also keep the facility row current: for example its latest status, a better-sourced coordinate (permit before EPA FRS before Compute Atlas), a new operator or tenant, or MW figures. The fact and its evidence still go in as an observation, which is append-only and can be superseded. The scoped facility update then keeps the row's "current" fields matching the latest evidence, so a reader of `/v1/facilities` doesn't have to work out the latest observation for each field. Identity and external IDs stay fill-only, because a change there usually means a wrong match, and that is a reviewer's call.

**What agents still can't do, and why:**
- **Change a Reviewed or Published row.** People have signed those off. A reviewer changes them on the server with `/v1/admin/rows/...`, and each change is audited. That includes applying an accepted correction to such a row.
- **Set review_status to Verified, Rejected, Reviewed or Published.** Those are reviewers' decisions. Agents may set only `Needs review` and `Conflict`.
- **Edit or delete an observation.** Observations are append-only; agents add a superseding one instead.
- **Overwrite fixed fields** (the table above). A different claim, item, limit or identity is a new row, or a reviewer's correction.
- **Erase a value with a blank.** Unknown is not zero, and not "remove" either.
- **Write `corrections` or `source_registry`.** Only admin can, because the corrections desk's decisions are made by people. The triage agent can still apply accepted changes to non-reviewed rows with its tracker or facility key.
- **Reach `/v1/admin/*`.** It is blocked from the internet.
