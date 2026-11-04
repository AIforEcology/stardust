# Data Center Resource Ledger

An open, source-cited record of every known data center and the power plants built for them: power sources, water sources and use, emissions, permits, subsidies and claims. Every fact carries its source, date, method and confidence. A project of [AI for Ecology](https://aifore.org).

This package is the ledger's database schema and API. It runs beside Stardust Core at `https://core.aifore.org/ledger/` but shares nothing with it.

- Design, schema, rules and API: [`docs/architecture/ledger.md`](../docs/architecture/ledger.md)
- Deploy, keys, import, backups and restore: [`deploy/README.md`](../deploy/README.md#data-center-resource-ledger)

## Layout

| Path | |
|---|---|
| `src/ledger/api.py` | FastAPI app: public reads, keyed writes, server-only admin |
| `src/ledger/store.py` | Every read and write, and the write rules (fill blanks only, idempotency, protected rows) |
| `src/ledger/db.py` | Connection pool, migrations, ID allocation |
| `src/ledger/models.py` | Tables, column types and value checks shared by the API and the importer |
| `src/ledger/auth.py` | `lk_` write keys (SHA-256 hashes only) |
| `src/ledger/importer.py` | Sheet tab → table column maps, validation and the upsert loader |
| `migrations/` | Numbered `.sql` files, applied in order at startup, tracked in `schema_migrations` |
| `scripts/import_sheet.py` | One-time import of the sheet's CSV exports, with a report |
| `tests/` | Tests against a real PostgreSQL + PostGIS; fixtures are synthetic |

## Develop

The tests need PostgreSQL 17 with PostGIS, for example:

```bash
docker run -d --name ledger-pg -e POSTGRES_PASSWORD=postgres -p 5432:5432 postgis/postgis:17-3.5   # amd64
python3 -m venv ~/.venvs/ledger && ~/.venvs/ledger/bin/pip install -e "ledger[dev]"
LEDGER_TEST_DSN=postgresql://postgres:postgres@localhost:5432/postgres ~/.venvs/ledger/bin/pytest ledger
```

On an arm64 machine, build the database image instead: `docker build -f deploy/ledger-db.Dockerfile -t ledger-db deploy`.

Run the API locally: `LEDGER_DATABASE_URL=postgresql://... LEDGER_ADMIN_TOKEN=dev uvicorn ledger.main:app --port 8090`.

Never commit sheet exports or `.env` values: the repository is public.
