"""Settings from the environment. Connection details use libpq's own PG* variables
(PGHOST, PGDATABASE, PGUSER, PGPASSWORD) unless LEDGER_DATABASE_URL is set."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

DEFAULT_MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    return int(raw) if raw else default


@dataclass(frozen=True)
class Settings:
    database_url: str = ""
    admin_token: Optional[str] = None
    migrations_dir: Path = DEFAULT_MIGRATIONS
    reads_per_ip_per_minute: int = 120
    writes_per_ip_per_minute: int = 120
    pool_min: int = 1
    pool_max: int = 10

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_url=os.environ.get("LEDGER_DATABASE_URL", ""),
            admin_token=os.environ.get("LEDGER_ADMIN_TOKEN") or None,
            migrations_dir=Path(os.environ.get("LEDGER_MIGRATIONS_DIR") or DEFAULT_MIGRATIONS),
            reads_per_ip_per_minute=_int("LEDGER_READS_PER_IP_PER_MINUTE", 120),
            writes_per_ip_per_minute=_int("LEDGER_WRITES_PER_IP_PER_MINUTE", 120),
            pool_min=_int("LEDGER_POOL_MIN", 1),
            pool_max=_int("LEDGER_POOL_MAX", 10),
        )
