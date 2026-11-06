"""Read facilities, the power plants that serve them, and on-site power facts from the ledger.

Two readers with the same interface and the same output shape:

- ``ApiLedger``: the public API, ``https://core.aifore.org/ledger/v1/...``. Read-only, no key.
- ``DbLedger``: the ledger's PostgreSQL database, for running on the server. It calls the ledger
  package's own query functions (ledger.store), so it returns exactly what the API would.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any, Dict, Iterator, List, Optional

import httpx

DEFAULT_LEDGER_URL = "https://core.aifore.org/ledger"
PAGE = 1000


def _plain(v: Any) -> Any:
    """Database values → the JSON types the API returns."""
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_plain(x) for x in v]
    return v


class ApiLedger:
    def __init__(self, base_url: str = DEFAULT_LEDGER_URL, client: Optional[httpx.Client] = None):
        self.base_url = base_url.rstrip("/")
        self._own = client is None
        self.client = client or httpx.Client(timeout=30, headers={"User-Agent": "stardust-intel"})

    def close(self) -> None:
        if self._own:
            self.client.close()

    def _get(self, path: str, **params: Any) -> Optional[Dict[str, Any]]:
        r = self.client.get(f"{self.base_url}{path}", params={k: v for k, v in params.items() if v is not None})
        if r.status_code == 404 and path.startswith("/v1/facilities/"):
            return None
        r.raise_for_status()
        return r.json()

    def describe(self) -> Dict[str, Any]:
        h = self._get("/healthz") or {}
        return {"source": self.base_url, "schema_version": h.get("schema_version"), "facilities": h.get("facilities")}

    def search(self, q: Optional[str] = None, state: Optional[str] = None) -> List[Dict[str, Any]]:
        return list(self._pages(q, state))

    def _pages(self, q: Optional[str], state: Optional[str]) -> Iterator[Dict[str, Any]]:
        offset: Optional[int] = 0
        while offset is not None:
            page = self._get("/v1/facilities", q=q, state=state, limit=PAGE, offset=offset) or {}
            yield from page.get("items", [])
            offset = page.get("next_offset")

    def facility(self, facility_id: str) -> Optional[Dict[str, Any]]:
        return self._get(f"/v1/facilities/{facility_id}")


class DbLedger:
    """Needs the ledger package (pip install -e ledger) and its database URL (LEDGER_DATABASE_URL)."""

    def __init__(self, dsn: str):
        try:
            from ledger import db, store  # type: ignore[import-not-found]
        except ImportError as e:  # pragma: no cover - depends on the environment
            raise RuntimeError("reading the database needs the ledger package: pip install -e ledger") from e
        self._store = store
        self._pool = db.make_pool(dsn, 1, 2)
        self.dsn_host = dsn.rsplit("@", 1)[-1]  # never the password

    def close(self) -> None:
        self._pool.close()

    def describe(self) -> Dict[str, Any]:
        with self._pool.connection() as conn:
            version = conn.execute("SELECT max(version) AS v FROM schema_migrations").fetchone()["v"]
            n = conn.execute("SELECT count(*) AS n FROM facilities").fetchone()["n"]
        return {"source": "database", "schema_version": version, "facilities": n}

    def search(self, q: Optional[str] = None, state: Optional[str] = None) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        with self._pool.connection() as conn:
            offset = 0
            while True:
                items, total = self._store.list_facilities(conn, q=q, state=state, limit=PAGE, offset=offset)
                out.extend(_plain(i) for i in items)
                offset += len(items)
                if not items or offset >= total:
                    return out

    def facility(self, facility_id: str) -> Optional[Dict[str, Any]]:
        with self._pool.connection() as conn:
            d = self._store.facility_detail(conn, facility_id)
        return _plain(d) if d is not None else None
