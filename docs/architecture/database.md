# Stardust Core database

How Stardust Core stores its data, why it's built this way, and how to run, operate and extend it. The code is in [`middleware/src/stardust_core/store.py`](../../middleware/src/stardust_core/store.py).

## Summary

- **Engine:** SQLite, through Python's standard `sqlite3` module. No database server, no extra dependencies, one file.
- **Mode:** write-ahead logging (WAL). Reads run alongside writes, and a crash loses nothing that was committed.
- **Default location:** the user data directory, not the repository. On macOS that's `~/Library/Application Support/stardust/core.db`. Override it with `STARDUST_DATABASE_PATH`.
- **Scale path:** the SQL is portable, so a PostgreSQL implementation of the same `Store` class can serve the hosted multi-tenant Core when it needs several instances writing at once.

## Why SQLite

Stardust Core has to be as small and easy to run as possible, because anyone should be able to self-host it (spec §9.3, §17). Its storage needs are modest and well defined:

| Need | What Core does |
|---|---|
| Append usage events | Thousands per second at most on a single instance |
| Never count an event twice | Clients retry, and SDK events can also arrive through an OpenTelemetry collector |
| Summaries and daily trends | By user, organization and time window (§6.3) |
| Broker state | Providers, quotes that must be single-use, orders (§10) |
| Privacy controls | Retention limit and per-user deletion (§14.1) |

SQLite covers all of this in one file with no server, which keeps Core a single process: `pip install` and run.

| Option | Why not (now) |
|---|---|
| PostgreSQL | Needs a server to install and run. It's the right choice once several Core instances must write concurrently, and the code is ready for it (see [Moving to PostgreSQL](#moving-to-postgresql)) |
| DuckDB | Excellent for analytics, but designed for bulk loads by one writer, not a steady stream of small inserts. A good tool for analysing exported event archives |
| TimescaleDB / InfluxDB | More infrastructure than the workload needs |
| An ORM (e.g. SQLAlchemy) | Adds dependencies and hides the schema. Plain SQL in one module is easier to read, port and review |

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `STARDUST_DATABASE_PATH` | user data directory, `…/stardust/core.db` | The database file. Its folder is created if missing |
| `STARDUST_RETENTION_DAYS` | unset (keep forever) | Delete events older than this many days. Runs at startup and every 6 hours |

Put the file on a **local, journaled disk** (APFS, ext4, NTFS). Avoid network filesystems (NFS, SMB), whose locking SQLite can't rely on. Also avoid exFAT or FAT USB drives, which have no journal, so unplugging or losing power mid-write can corrupt the file.

Programmatic use (and the test suite) can pass `database_path=None` to `Settings` for a private in-memory database.

## Schema

The schema is version 2, recorded in `PRAGMA user_version`. Every table stores its full record as JSON in a `data` column; that's the source of truth. Next to it are copies of the fields that queries filter, sort or add up, so new fields can be added to the models without a migration.

### `events`

One row per enriched usage event (spec §7).

| Column | Type | Notes |
|---|---|---|
| `event_id` | TEXT, primary key | UUID. The uniqueness here is what stops double counting |
| `ts` | TEXT | Event time as UTC ISO 8601, so text order is time order |
| `user_id`, `org_id` | TEXT | Pseudonymous UUIDs (§14.1), nullable |
| `source_layer`, `provider`, `model`, `region` | TEXT | |
| `tokens_in`, `tokens_out`, `tokens_cached_in` | INTEGER | Nullable |
| `cost_usd` | REAL | **NULL when the price is unknown**, never 0, so totals and "unknown cost" counts stay honest |
| `energy_wh`, `co2e_g`, `water_ml` | REAL | |
| `confidence_tier`, `indicator_code` | TEXT | |
| `data` | TEXT | The complete `EnrichedEvent` JSON (SJI, grid mix, methodology version…) |

Indexes: `(ts)`, `(user_id, ts)`, `(org_id, ts)`.

### `providers`, `quotes`, `orders`

These hold the Remediation Broker's state (spec §10).

| Table | Key | Query columns | Notes |
|---|---|---|---|
| `providers` | `provider_id` | `status` | Vetting status and assigned tier. Loaded into memory at startup, because the set is small and read on every quote |
| `quotes` | `quote_id` | `expires_at` (indexed) | Deleted when used or expired |
| `orders` | `order_id` | `subscriber_id`, `status`, `created_at`, `provider_price_usd`, `fee_usd`, `total_usd` (indexed by subscriber and by `created_at`) | Updated as fulfillment progresses. The money columns are fixed from the quote at order time and feed the tech operations fee report |

## How writes work

### Recording an event

`POST /v1/events`, the OTLP receivers (HTTP and gRPC), and the SDKs all end up in one function:

1. Under a process-wide lock, look up `event_id`. If the event is already stored, return the stored copy. That's how a retry, or an SDK event that also arrives through a collector, is counted once, even across restarts.
2. Otherwise enrich it (cost, energy, CO₂e, water, indicator code) and `INSERT … ON CONFLICT (event_id) DO NOTHING`.
3. Export it to OpenTelemetry, outside the lock.

The lock makes "check, enrich, insert" atomic. Concurrent retries of the same event all receive the same enriched event: the same SJI, the same figures.

### Single-use quotes

Ordering takes the quote with `SELECT` then `DELETE` inside one `BEGIN IMMEDIATE` transaction. Of two simultaneous orders for one quote, exactly one gets it. The test suite races ten threads on one quote to check this.

### Concurrency model

Core opens **one connection**, shared across FastAPI's thread pool and the gRPC receiver's event loop and serialized by a lock. SQLite allows one writer at a time anyway, and Core's statements take well under a millisecond, so a connection pool would add complexity without adding throughput.

### Durability

- `journal_mode=WAL`: commits go to `core.db-wal` and are folded into `core.db` automatically (checkpoints).
- `synchronous=NORMAL`: in WAL mode, a committed transaction survives a process crash. A power loss can drop only the last few commits, never corrupt the file.
- `busy_timeout=5000`: another process holding the write lock (e.g. a backup) makes Core wait up to 5 s instead of failing.

**Tested:** 200 events were sent to a running Core, which was then killed with `kill -9`. After a restart on the same file, all 200 were there, with identical totals.

## How reads work

| Endpoint | Query |
|---|---|
| `GET /v1/events?user_id=&limit=` | Newest first, via the `(user_id, ts)` or `(ts)` index |
| `GET /v1/summary?user_id=&org_id=&since=&until=` | One `SUM`/`COUNT` aggregate; the indicator code is computed from the totals |
| `GET /v1/summary/daily?…` | The same totals grouped by UTC day, for trend charts |

`since` is inclusive and `until` exclusive. Both accept any ISO 8601 time with an offset and are compared in UTC.

## Privacy and retention (spec §14.1)

- **Retention:** set `STARDUST_RETENTION_DAYS`, and events older than that are deleted at startup and every 6 hours. Expired quotes are cleaned up on the same schedule.
- **Deletion on request:** `DELETE /v1/admin/users/{user_id}/events` erases one user's events. It requires the admin token.
- **Content:** events never contain prompts or responses. Only counts, model, region and time are received in the first place.

## Performance

Measured on an Apple M4 Mac (16 GB) with 20,000 realistic events, Python 3.9, one event per transaction:

| | |
|---|---|
| Inserts | ~14,000 events per second |
| Size | ~1.6 KB per event, including indexes and the full JSON copy (≈1.6 GB per million events) |
| One user's summary | 0.5 ms |
| 50 most recent events | 0.4 ms |
| Daily totals over all 20,000 events | 8.5 ms |

Most of each row is the JSON copy, and within it the per-event grid mix. If size matters more than self-contained rows, the options are: store the mix only when it differs from the methodology's regional default; roll old events up into daily totals and delete them (with retention); or compress the `data` column.

## Operating it

**Inspect** with the `sqlite3` command-line tool:

```bash
sqlite3 ~/Library/Application\ Support/stardust/core.db \
  "SELECT provider, COUNT(*), ROUND(SUM(co2e_g), 3) FROM events GROUP BY provider"
```

**Back up** while Core is running. Copying the file alone could miss recent writes that are still in the WAL, so use SQLite's online backup:

```bash
sqlite3 ~/Library/Application\ Support/stardust/core.db ".backup '/path/to/backup.db'"
```

For continuous off-site backup, [Litestream](https://litestream.io) streams the WAL to S3-compatible storage and restores to any point in time.

**Move or reset:** stop Core, then move or delete `core.db` together with `core.db-wal` and `core.db-shm`.

**Health:** `GET /healthz` reports the database path, schema version and row counts per table.

## Changing the schema

1. Append a new SQL script to `_MIGRATIONS` in `store.py` and increase `SCHEMA_VERSION`.
2. On startup, Core runs every migration newer than the file's `user_version`, each in its own transaction, and records the new version.
3. Core refuses to open a database with a **newer** schema than it understands, so an older build can't damage data written by a newer one.

Example: v2 added the fee columns to `orders` with three `ALTER TABLE … ADD COLUMN` statements and an index. Existing orders keep NULL in the new columns, and the fee report counts them as fee-free.

Prefer adding columns (and back-filling from `data` if needed) over rewriting tables. Since `data` holds the full record, most model changes need no migration at all.

## Moving to PostgreSQL

Switch when you run **more than one Core instance writing to the same data**, typically the hosted multi-tenant service (§9.3). A single instance, even a busy one, doesn't need it.

`Store` is the only class that touches the database. A `PostgresStore` with the same methods needs these changes:

| SQLite | PostgreSQL |
|---|---|
| `?` placeholders | `%s` (psycopg) or `$1` (asyncpg) |
| `TEXT` ids and times | `UUID`, `TIMESTAMPTZ` |
| `TEXT` JSON | `JSONB` |
| `PRAGMA` settings, `user_version` | Drop the PRAGMAs; keep the version in a `schema_version` table |
| `BEGIN IMMEDIATE` + `SELECT`/`DELETE` for quotes | `DELETE … RETURNING` (one atomic statement) |
| Process-wide lock around check-and-insert | Unnecessary: `INSERT … ON CONFLICT DO NOTHING RETURNING` decides the winner across instances |

`ON CONFLICT`, the indexes and all queries carry over unchanged. Selecting the backend by URL (`STARDUST_DATABASE_URL=postgresql://…`) is the planned interface.

## What isn't in the database

| Data | Where it lives |
|---|---|
| Methodology factors and thresholds | Versioned JSON in [`schema/factors/`](../../schema/factors/) (§8.4) |
| Model prices | litellm's pricing file: a vendored copy plus a refreshed cache (see the middleware README) |
| Dashboard metrics | Exported as OpenTelemetry metrics, not stored; see [`schema/otel-attributes.md`](../../schema/otel-attributes.md) |
| Extension conversation totals | In each user's browser (`chrome.storage.local`), as numbers only |
