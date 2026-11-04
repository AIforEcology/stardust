#!/usr/bin/env python3
"""Load the Data Center Resource Ledger's Google Sheet into the ledger database, once.

1. In Google Sheets, export each tab as CSV (File > Download > Comma-separated values) into one
   local folder. Keep that folder out of the repository: the sheet holds unreviewed notes.
2. Run one of:

   # On the server, straight into the database (inside the ledger container):
   docker compose cp ./sheet-export ledger:/tmp/sheet-export
   docker compose exec ledger python /app/ledger/scripts/import_sheet.py /tmp/sheet-export --dsn ""

   # Or through the admin import endpoint (only reachable from the server):
   docker compose exec ledger sh -c 'python /app/ledger/scripts/import_sheet.py /tmp/sheet-export \\
       --api http://127.0.0.1:8090 --admin-token "$LEDGER_ADMIN_TOKEN"'

   --dsn "" uses the container's PG* environment variables. --dry-run validates without loading.

Every existing ID is kept. Rows are upserted by ID, so running it again changes nothing that is
already there. A report (Markdown and JSON) lists rows loaded, rows rejected and why, duplicate IDs,
coordinates outside the US and bad confidence values.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ledger import db, importer  # noqa: E402


def post(api: str, token: str, payload: dict) -> dict:
    req = urllib.request.Request(api.rstrip("/") + "/v1/admin/import", data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json", "X-Ledger-Admin-Token": token},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=300) as r:  # nosec B310 - operator-supplied URL
        return json.load(r)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", type=Path, help="folder of CSV exports, one per tab")
    target = ap.add_mutually_exclusive_group()
    target.add_argument("--dsn", help='PostgreSQL connection string ("" = PG* environment variables)')
    target.add_argument("--api", help="ledger base URL for the admin import endpoint, e.g. http://127.0.0.1:8090")
    ap.add_argument("--admin-token", help="LEDGER_ADMIN_TOKEN, with --api")
    ap.add_argument("--report", type=Path, default=Path("import-report"),
                    help="report path without extension (writes .md and .json); default ./import-report")
    ap.add_argument("--dry-run", action="store_true", help="validate and report, load nothing")
    ap.add_argument("--batch-size", type=int, default=1000)
    args = ap.parse_args(argv)

    if not args.folder.is_dir():
        ap.error(f"{args.folder} is not a folder")
    if not args.dry_run and args.dsn is None and not args.api:
        ap.error("give --dsn or --api (or --dry-run)")
    if args.api and not args.admin_token:
        ap.error("--api needs --admin-token")

    batches, report = importer.prepare(args.folder)
    if not args.dry_run:
        if args.api:
            for b in batches:
                for i in range(0, len(b.rows), args.batch_size):
                    chunk = importer.Batch(b.table, b.mode, b.rows[i:i + args.batch_size], b.tab,
                                           b.row_numbers[i:i + args.batch_size])
                    res = post(args.api, args.admin_token,
                               {"table": b.table, "mode": b.mode, "rows": importer.jsonable(chunk.rows)})
                    importer.record(report, chunk, res["results"])
        else:
            import psycopg
            from psycopg.rows import dict_row
            from psycopg.types.json import Jsonb
            with psycopg.connect(args.dsn, row_factory=dict_row) as conn:
                db.migrate(conn, db_migrations())
                with conn.transaction():
                    conn.execute("SELECT set_config('ledger.admin', 'on', true), "
                                 "set_config('ledger.key_id', 'import-script', true)")
                    for b in batches:
                        importer.record(report, b, importer.load(conn, b.table, b.mode, b.rows))
                    db.sync_counters(conn)
                    conn.execute("INSERT INTO audit_log (key_id, action, table_name, after) VALUES "
                                 "('import-script', 'import', 'all', %s)",
                                 (Jsonb({"loaded": report.loaded, "rejected": len(report.rejected)}),))

    args.report.with_suffix(".md").write_text(report.markdown())
    args.report.with_suffix(".json").write_text(json.dumps(report.to_dict(), indent=2, default=str))
    print(f"loaded/updated: {sum(report.loaded.values())}, unchanged: {sum(report.unchanged.values())}, "
          f"rejected: {len(report.rejected)}, duplicate IDs: {len(report.duplicate_ids)}, "
          f"outside US: {len(report.outside_us)}, bad confidence: {len(report.bad_confidence)}")
    print(f"report: {args.report.with_suffix('.md')}")
    return 0


def db_migrations() -> Path:
    from ledger.config import Settings
    return Settings.from_env().migrations_dir


if __name__ == "__main__":
    sys.exit(main())
