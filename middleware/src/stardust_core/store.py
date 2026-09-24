"""Persistent storage for Stardust Core: SQLite (spec §9.3, §14.1, §15).

One file, no server, no dependencies beyond Python's standard library. WAL mode lets
reads run alongside the single writer. The SQL is deliberately plain (no SQLite-only
syntax beyond the PRAGMAs and ``?`` placeholders) so a PostgreSQL implementation of the
same class can follow when the hosted multi-tenant Core needs several writers.

Each table keeps its record as JSON (the source of truth, so the model can grow without a
migration) plus the columns that queries filter, sort or aggregate on.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple, Union
from uuid import UUID

from .models import EnrichedEvent

log = logging.getLogger(__name__)

# Bump when adding a migration; migrations run in order on open.
SCHEMA_VERSION = 1

_MIGRATIONS: List[str] = [
    # v1
    """
    CREATE TABLE events (
        event_id        TEXT PRIMARY KEY,
        ts              TEXT NOT NULL,      -- UTC ISO 8601, sortable
        user_id         TEXT,
        org_id          TEXT,
        source_layer    TEXT NOT NULL,
        provider        TEXT NOT NULL,
        model           TEXT NOT NULL,
        region          TEXT,
        tokens_in       INTEGER,
        tokens_out      INTEGER,
        tokens_cached_in INTEGER,
        cost_usd        REAL,               -- NULL when the price is unknown
        energy_wh       REAL NOT NULL,
        co2e_g          REAL NOT NULL,
        water_ml        REAL NOT NULL,
        confidence_tier TEXT NOT NULL,
        indicator_code  TEXT NOT NULL,
        data            TEXT NOT NULL       -- the full EnrichedEvent as JSON
    );
    CREATE INDEX events_ts ON events (ts);
    CREATE INDEX events_user_ts ON events (user_id, ts);
    CREATE INDEX events_org_ts ON events (org_id, ts);

    CREATE TABLE providers (
        provider_id TEXT PRIMARY KEY,
        status      TEXT NOT NULL,
        data        TEXT NOT NULL
    );

    CREATE TABLE quotes (
        quote_id   TEXT PRIMARY KEY,
        expires_at TEXT NOT NULL,
        data       TEXT NOT NULL
    );
    CREATE INDEX quotes_expires ON quotes (expires_at);

    CREATE TABLE orders (
        order_id      TEXT PRIMARY KEY,
        subscriber_id TEXT NOT NULL,
        status        TEXT NOT NULL,
        created_at    TEXT NOT NULL,
        data          TEXT NOT NULL
    );
    CREATE INDEX orders_subscriber ON orders (subscriber_id, created_at);
    """,
]


def default_database_path() -> Path:
    """Per-user data directory, never inside the repo (and so never on a removable drive by accident)."""
    import os
    import sys

    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "stardust" / "core.db"


def _utc(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).isoformat()


def _json(obj: Any) -> str:
    return json.dumps(obj, default=str, separators=(",", ":"))


@dataclass(frozen=True)
class Aggregate:
    """Totals over a set of events, as the summary endpoint and indicator code need them."""

    events: int
    tokens_in: int
    tokens_out: int
    tokens_cached_in: int
    cost_usd: float  # over events with a known price
    cost_known_events: int
    energy_wh: float
    co2e_g: float
    water_ml: float


class Store:
    """Thread-safe SQLite store. ``path=":memory:"`` gives a private in-memory database (tests)."""

    def __init__(self, path: Union[str, Path] = ":memory:"):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        # One connection, serialized by a lock: SQLite has a single writer anyway, and Core's
        # queries are short. check_same_thread=False because FastAPI's thread pool and the
        # gRPC receiver's event loop both call in.
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            if self.path != ":memory:":
                self._conn.execute("PRAGMA journal_mode=WAL")
            # NORMAL is durable in WAL mode except for the last transactions on power loss.
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._migrate()

    # --- lifecycle ----------------------------------------------------------------------

    def _migrate(self) -> None:
        version = self._conn.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise RuntimeError(f"{self.path} has schema v{version}; this Core understands up to v{SCHEMA_VERSION}")
        for target in range(version + 1, SCHEMA_VERSION + 1):
            with self._transaction():
                for statement in _MIGRATIONS[target - 1].split(";"):
                    if statement.strip():
                        self._conn.execute(statement)
                self._conn.execute(f"PRAGMA user_version={target}")
            log.info("Database %s migrated to schema v%d", self.path, target)

    class _Tx:
        def __init__(self, conn: sqlite3.Connection):
            self.conn = conn

        def __enter__(self) -> sqlite3.Connection:
            self.conn.execute("BEGIN IMMEDIATE")
            return self.conn

        def __exit__(self, exc_type: Any, *_: Any) -> None:
            self.conn.execute("ROLLBACK" if exc_type else "COMMIT")

    def _transaction(self) -> "Store._Tx":
        return Store._Tx(self._conn)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # --- events --------------------------------------------------------------------------

    def get_event(self, event_id: UUID) -> Optional[EnrichedEvent]:
        with self._lock:
            row = self._conn.execute("SELECT data FROM events WHERE event_id = ?", (str(event_id),)).fetchone()
        return EnrichedEvent.model_validate_json(row["data"]) if row else None

    def insert_event(self, e: EnrichedEvent) -> bool:
        """Store an event. Returns False (and stores nothing) if its event_id already exists."""
        with self._lock:
            cur = self._conn.execute(
                """INSERT INTO events (event_id, ts, user_id, org_id, source_layer, provider, model, region,
                       tokens_in, tokens_out, tokens_cached_in, cost_usd, energy_wh, co2e_g, water_ml,
                       confidence_tier, indicator_code, data)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT (event_id) DO NOTHING""",
                (
                    str(e.event_id), _utc(e.timestamp),
                    str(e.user_id) if e.user_id else None, str(e.org_id) if e.org_id else None,
                    e.source_layer.value, e.provider, e.model, e.region,
                    e.tokens_in, e.tokens_out, e.tokens_cached_in, e.cost_usd,
                    e.energy_wh, e.co2e_g, e.water_ml, e.confidence_tier.value, e.indicator_code,
                    e.model_dump_json(),
                ),
            )
            return cur.rowcount == 1

    def recent_events(self, *, user_id: Optional[UUID] = None, limit: int = 50) -> List[EnrichedEvent]:
        sql, args = "SELECT data FROM events", []
        if user_id is not None:
            sql += " WHERE user_id = ?"
            args.append(str(user_id))
        sql += " ORDER BY ts DESC, rowid DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [EnrichedEvent.model_validate_json(r["data"]) for r in rows]

    def aggregate(self, *, user_id: Optional[UUID] = None, org_id: Optional[UUID] = None,
                  since: Optional[datetime] = None, until: Optional[datetime] = None) -> Aggregate:
        where, args = self._filters(user_id, org_id, since, until)
        with self._lock:
            r = self._conn.execute(
                f"""SELECT COUNT(*) AS events,
                          COALESCE(SUM(tokens_in), 0) AS tokens_in,
                          COALESCE(SUM(tokens_out), 0) AS tokens_out,
                          COALESCE(SUM(tokens_cached_in), 0) AS tokens_cached_in,
                          COALESCE(SUM(cost_usd), 0) AS cost_usd,
                          COUNT(cost_usd) AS cost_known_events,
                          COALESCE(SUM(energy_wh), 0) AS energy_wh,
                          COALESCE(SUM(co2e_g), 0) AS co2e_g,
                          COALESCE(SUM(water_ml), 0) AS water_ml
                   FROM events {where}""",
                args,
            ).fetchone()
        return Aggregate(**{k: r[k] for k in r.keys()})

    def daily(self, *, user_id: Optional[UUID] = None, org_id: Optional[UUID] = None,
              since: Optional[datetime] = None, until: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """Per-day (UTC) totals for dashboards (§6.3)."""
        where, args = self._filters(user_id, org_id, since, until)
        with self._lock:
            rows = self._conn.execute(
                f"""SELECT substr(ts, 1, 10) AS day, COUNT(*) AS events,
                          COALESCE(SUM(tokens_in), 0) AS tokens_in, COALESCE(SUM(tokens_out), 0) AS tokens_out,
                          COALESCE(SUM(cost_usd), 0) AS cost_usd, COALESCE(SUM(energy_wh), 0) AS energy_wh,
                          COALESCE(SUM(co2e_g), 0) AS co2e_g, COALESCE(SUM(water_ml), 0) AS water_ml
                   FROM events {where} GROUP BY day ORDER BY day""",
                args,
            ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def _filters(user_id: Optional[UUID], org_id: Optional[UUID], since: Optional[datetime],
                 until: Optional[datetime]) -> Tuple[str, List[Any]]:
        clauses, args = [], []
        if user_id is not None:
            clauses.append("user_id = ?")
            args.append(str(user_id))
        if org_id is not None:
            clauses.append("org_id = ?")
            args.append(str(org_id))
        if since is not None:
            clauses.append("ts >= ?")
            args.append(_utc(since))
        if until is not None:
            clauses.append("ts < ?")
            args.append(_utc(until))
        return ("WHERE " + " AND ".join(clauses)) if clauses else "", args

    def delete_user_events(self, user_id: UUID) -> int:
        """Erase a user's events (§14.1 deletion controls). Returns how many were deleted."""
        with self._lock:
            return self._conn.execute("DELETE FROM events WHERE user_id = ?", (str(user_id),)).rowcount

    def purge_events_before(self, cutoff: datetime) -> int:
        """Retention limit (§14.1): delete events older than ``cutoff``."""
        with self._lock:
            return self._conn.execute("DELETE FROM events WHERE ts < ?", (_utc(cutoff),)).rowcount

    # --- broker: providers, quotes, orders ------------------------------------------------------

    def save_provider(self, provider_id: str, status: str, data: Dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT INTO providers (provider_id, status, data) VALUES (?, ?, ?)
                   ON CONFLICT (provider_id) DO UPDATE SET status = excluded.status, data = excluded.data""",
                (provider_id, status, _json(data)),
            )

    def load_providers(self) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT data FROM providers ORDER BY provider_id").fetchall()
        return [json.loads(r["data"]) for r in rows]

    def save_quote(self, quote_id: UUID, expires_at: datetime, data: Dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO quotes (quote_id, expires_at, data) VALUES (?, ?, ?)",
                (str(quote_id), _utc(expires_at), _json(data)),
            )

    def take_quote(self, quote_id: UUID) -> Optional[Dict[str, Any]]:
        """Remove and return a quote. Atomic: of two concurrent orders for one quote, only one gets it."""
        with self._lock, self._transaction() as conn:
            row = conn.execute("SELECT data FROM quotes WHERE quote_id = ?", (str(quote_id),)).fetchone()
            if row is None:
                return None
            conn.execute("DELETE FROM quotes WHERE quote_id = ?", (str(quote_id),))
        return json.loads(row["data"])

    def purge_expired_quotes(self, now: Optional[datetime] = None) -> int:
        with self._lock:
            return self._conn.execute(
                "DELETE FROM quotes WHERE expires_at < ?", (_utc(now or datetime.now(timezone.utc)),)
            ).rowcount

    def save_order(self, order_id: UUID, subscriber_id: str, status: str, created_at: datetime,
                   data: Dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT INTO orders (order_id, subscriber_id, status, created_at, data) VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT (order_id) DO UPDATE SET status = excluded.status, data = excluded.data""",
                (str(order_id), subscriber_id, status, _utc(created_at), _json(data)),
            )

    def load_order(self, order_id: UUID) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute("SELECT data FROM orders WHERE order_id = ?", (str(order_id),)).fetchone()
        return json.loads(row["data"]) if row else None

    # --- maintenance ------------------------------------------------------------------------------

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            counts = {t: self._conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]  # noqa: S608 - fixed names
                      for t in ("events", "providers", "quotes", "orders")}
            version = self._conn.execute("PRAGMA user_version").fetchone()[0]
        return {"path": self.path, "schema_version": version, "rows": counts}

    def retention_cutoff(self, days: int) -> datetime:
        return datetime.now(timezone.utc) - timedelta(days=days)

    def __iter__(self) -> Iterator[EnrichedEvent]:  # pragma: no cover - convenience for exports
        with self._lock:
            rows = self._conn.execute("SELECT data FROM events ORDER BY ts").fetchall()
        return (EnrichedEvent.model_validate_json(r["data"]) for r in rows)
