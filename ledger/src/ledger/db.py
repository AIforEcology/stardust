"""Connections, migrations and ID allocation. Plain SQL with psycopg 3; no ORM."""

from __future__ import annotations

import logging
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, List, Optional, Tuple

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

log = logging.getLogger("ledger.db")

MIGRATION_RE = re.compile(r"^(\d{3,})_[a-z0-9_]+\.sql$")
# Any constant works; it only has to be the same in every process that migrates this database.
MIGRATION_LOCK = 0x1ED6E5


def make_pool(dsn: str, min_size: int = 1, max_size: int = 10) -> ConnectionPool:
    pool = ConnectionPool(dsn, min_size=min_size, max_size=max_size, open=False,
                          kwargs={"row_factory": dict_row, "autocommit": False})
    pool.open(wait=True, timeout=30)
    return pool


def migration_files(directory: Path) -> List[Tuple[int, str, Path]]:
    out = []
    for p in sorted(directory.glob("*.sql")):
        m = MIGRATION_RE.match(p.name)
        if not m:
            raise RuntimeError(f"migration file name must look like 001_name.sql: {p.name}")
        out.append((int(m.group(1)), p.name, p))
    versions = [v for v, _, _ in out]
    if len(versions) != len(set(versions)):
        raise RuntimeError("two migrations share a number")
    return sorted(out)


def migrate(conn: psycopg.Connection, directory: Path) -> List[str]:
    """Apply every migration not yet in schema_migrations, in order, each in its own transaction.
    An advisory lock makes concurrent starts (several workers, tests) safe. Returns what it applied."""
    applied_now: List[str] = []
    conn.autocommit = True
    try:
        conn.execute("SELECT pg_advisory_lock(%s)", (MIGRATION_LOCK,))
        try:
            conn.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
                                version    integer PRIMARY KEY,
                                name       text NOT NULL,
                                applied_at timestamptz NOT NULL DEFAULT now())""")
            done = {r[0] if not isinstance(r, dict) else r["version"]
                    for r in conn.execute("SELECT version FROM schema_migrations").fetchall()}
            for version, name, path in migration_files(directory):
                if version in done:
                    continue
                sql = path.read_text()
                with conn.transaction():
                    conn.execute(sql)  # no parameters, so several statements are allowed
                    conn.execute("INSERT INTO schema_migrations (version, name) VALUES (%s, %s)", (version, name))
                log.info("applied migration %s", name)
                applied_now.append(name)
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (MIGRATION_LOCK,))
    finally:
        conn.autocommit = False
    return applied_now


@contextmanager
def transaction(pool: ConnectionPool, *, admin_key_id: Optional[str] = None) -> Iterator[psycopg.Connection]:
    """A connection inside one transaction. With ``admin_key_id`` the transaction is marked as an
    admin one: triggers then allow changes to Reviewed/Published rows and write them to audit_log."""
    with pool.connection() as conn:
        with conn.transaction():
            if admin_key_id:
                conn.execute("SELECT set_config('ledger.admin', 'on', true), set_config('ledger.key_id', %s, true)",
                             (admin_key_id,))
            yield conn


def next_number(conn: psycopg.Connection, prefix: str, country: str = "", state: str = "") -> int:
    return conn.execute("SELECT ledger_next_number(%s, %s, %s) AS n", (prefix, country, state)).fetchone()["n"]


def allocate_facility_id(conn: psycopg.Connection, prefix: str, state: str) -> str:
    """Next ID for (prefix, country, state), e.g. DC-US-TX-0153. state is ISO 3166-2 (US-TX)."""
    country, _, sub = state.partition("-")
    while True:
        n = next_number(conn, prefix, country, sub)
        fid = f"{prefix}-{country}-{sub}-{n:04d}"
        taken = conn.execute("""SELECT 1 FROM facilities WHERE facility_id = %(id)s
                                UNION ALL SELECT 1 FROM facility_aliases WHERE alias_id = %(id)s""",
                             {"id": fid}).fetchone()
        if not taken:  # only after an import that skipped sync_counters
            return fid


def allocate_sequence_id(conn: psycopg.Connection, table: str, pk: str, prefix: str) -> str:
    while True:
        n = next_number(conn, prefix)
        rid = f"{prefix}-{n:04d}"
        if not conn.execute(f"SELECT 1 FROM {table} WHERE {pk} = %s", (rid,)).fetchone():  # nosec B608
            return rid


def sync_counters(conn: psycopg.Connection) -> None:
    """Raise every counter to the highest number already used, after an import that kept IDs."""
    conn.execute(r"""
        INSERT INTO id_counters AS ic (prefix, country, state, last_number)
        SELECT m[1], m[2], m[3], max(m[4]::int)
        FROM (SELECT regexp_match(id, '^(DC|PWR)-([A-Z]{2})-([A-Z0-9]{1,3})-([0-9]+)$') AS m
              FROM (SELECT facility_id AS id FROM facilities UNION ALL SELECT alias_id FROM facility_aliases) ids) s
        WHERE m IS NOT NULL
        GROUP BY 1, 2, 3
        ON CONFLICT (prefix, country, state) DO UPDATE SET last_number = greatest(ic.last_number, excluded.last_number)
    """)
    for table, pk, prefix in (("observations", "obs_id", "OBS"), ("claims", "claim_id", "CLM"),
                              ("who_pays", "item_id", "WP"), ("permit_checks", "check_id", "PVA")):
        conn.execute(f"""
            INSERT INTO id_counters AS ic (prefix, country, state, last_number)
            SELECT %(p)s, '', '', max(substring({pk} from '[0-9]+$')::int) FROM {table}
            HAVING count(*) > 0
            ON CONFLICT (prefix, country, state) DO UPDATE SET last_number = greatest(ic.last_number, excluded.last_number)
        """, {"p": prefix})  # nosec B608 - fixed table names
