"""Reads and writes. Every write goes through here, so the rules live in one place:

- IDs are assigned by the server, inside the write's transaction (db.allocate_*).
- A retry with the same idempotency_key (or, for observations, the same facility, field, value and
  source URL) returns the row the first attempt made instead of adding another.
- Ordinary writes only fill blank fields. Overwriting a filled field, or touching a Reviewed or
  Published row, raises Conflict (HTTP 409); only the admin endpoints may, and the database's
  triggers write every admin change to audit_log.
- Observations are never updated or deleted here; a correction is a new row with ``supersedes``.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence, Tuple

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from . import db
from .models import PROTECTED_STATUSES, TABLES, Table, ValueProblem, clean

FACILITY_JSON = sql.SQL("""(to_jsonb(f) - 'geom' - 'idempotency_key')
    || jsonb_build_object('latitude', ST_Y(f.geom::geometry), 'longitude', ST_X(f.geom::geometry))""")


class Conflict(Exception):
    def __init__(self, reason: str, message: str, fields: Sequence[str] = ()):
        super().__init__(message)
        self.reason, self.message, self.fields = reason, message, list(fields)


class NotFound(Exception):
    pass


# --- helpers ------------------------------------------------------------------------------------

def _empty(v: Any) -> bool:
    return v is None or v == "" or v == []


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, float) or isinstance(b, float):
        try:
            return a is not None and b is not None and abs(float(a) - float(b)) < 1e-9
        except (TypeError, ValueError):
            return False
    if isinstance(a, Decimal) or isinstance(b, Decimal):
        try:
            return a is not None and b is not None and Decimal(str(a)) == Decimal(str(b))
        except Exception:
            return False
    return a == b


def _values(table: str, values: Dict[str, Any]) -> Tuple[List[sql.Composable], List[sql.Composable], List[Any]]:
    """Column identifiers, value expressions and parameters for an INSERT. Facilities take
    latitude/longitude and store them as one geography point."""
    values = dict(values)
    cols: List[sql.Composable] = []
    exprs: List[sql.Composable] = []
    params: List[Any] = []
    if table == "facilities" and ("latitude" in values or "longitude" in values):
        lat, lon = values.pop("latitude", None), values.pop("longitude", None)
        if (lat is None) != (lon is None):
            raise ValueProblem("latitude", "latitude and longitude go together")
        if lat is not None:
            cols.append(sql.Identifier("geom"))
            exprs.append(sql.SQL("ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography"))
            params.extend([lon, lat])
    for k, v in values.items():
        cols.append(sql.Identifier(k))
        exprs.append(sql.SQL("%s"))
        params.append(v)
    return cols, exprs, params


def _lock(conn: psycopg.Connection, *parts: str) -> None:
    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (":".join(parts),))


def _raw(conn: psycopg.Connection, table: Table, pk_value: Any, *, for_update: bool = False) -> Optional[dict]:
    extra = sql.SQL(", ST_Y(geom::geometry) AS latitude, ST_X(geom::geometry) AS longitude") \
        if table.name == "facilities" else sql.SQL("")
    q = sql.SQL("SELECT *{} FROM {} WHERE {} = %s{}").format(
        extra, sql.Identifier(table.name), sql.Identifier(table.pk), sql.SQL(" FOR UPDATE") if for_update else sql.SQL(""))
    return conn.execute(q, (pk_value,)).fetchone()


def public_row(conn: psycopg.Connection, table: Table, pk_value: Any) -> Optional[dict]:
    if table.name == "facilities":
        q = sql.SQL("SELECT {} AS row FROM facilities f WHERE facility_id = %s").format(FACILITY_JSON)
    else:
        q = sql.SQL("SELECT to_jsonb(t) - 'idempotency_key' AS row FROM {} t WHERE {} = %s").format(
            sql.Identifier(table.name), sql.Identifier(table.pk))
    r = conn.execute(q, (pk_value,)).fetchone()
    return r["row"] if r else None


def _by_idempotency_key(conn: psycopg.Connection, table: Table, key: str) -> Optional[Any]:
    r = conn.execute(sql.SQL("SELECT {} AS pk FROM {} WHERE idempotency_key = %s").format(
        sql.Identifier(table.pk), sql.Identifier(table.name)), (key,)).fetchone()
    return r["pk"] if r else None


def _insert(conn: psycopg.Connection, table: Table, values: Dict[str, Any]) -> Any:
    cols, exprs, params = _values(table.name, values)
    q = sql.SQL("INSERT INTO {} ({}) VALUES ({}) RETURNING {} AS pk").format(
        sql.Identifier(table.name), sql.SQL(", ").join(cols), sql.SQL(", ").join(exprs), sql.Identifier(table.pk))
    return conn.execute(q, params).fetchone()["pk"]


def _update(conn: psycopg.Connection, table: Table, pk_value: Any, changes: Dict[str, Any]) -> None:
    cols, exprs, params = _values(table.name, changes)
    sets = sql.SQL(", ").join(sql.SQL("{} = {}").format(c, e) for c, e in zip(cols, exprs))
    conn.execute(sql.SQL("UPDATE {} SET {} WHERE {} = %s").format(
        sql.Identifier(table.name), sets, sql.Identifier(table.pk)), params + [pk_value])


def _check_status(data: Dict[str, Any], admin: bool) -> None:
    if not admin and data.get("review_status") in PROTECTED_STATUSES:
        raise Conflict("protected_status", f"only an admin may set review_status to {data['review_status']}",
                       ["review_status"])


def _fill(conn: psycopg.Connection, table: Table, pk_value: Any, data: Dict[str, Any], *,
          admin: bool) -> Tuple[dict, bool]:
    """Fill blank fields of an existing row; refuse to overwrite filled ones unless admin."""
    row = _raw(conn, table, pk_value, for_update=True)
    if row is None:
        raise NotFound(f"{table.name} {pk_value} not found")
    if not admin and row.get("review_status") in PROTECTED_STATUSES:
        raise Conflict("row_protected", f"{table.name} {pk_value} is {row['review_status']}; only an admin may change it "
                       "(use /v1/admin on the server)")
    _check_status(data, admin)
    changes, conflicts = {}, []
    for k, v in data.items():
        if k == table.pk:
            continue
        old = row.get(k)
        if _same(old, v) or (_empty(old) and _empty(v)):
            continue
        if admin or _empty(old):
            changes[k] = v
        else:
            conflicts.append(k)
    if conflicts:
        raise Conflict("field_filled", f"{table.name} {pk_value} already has a value for {', '.join(conflicts)}; "
                       "ordinary keys only fill blank fields. Add an observation, or ask an admin to change it",
                       conflicts)
    if table.name == "facilities" and ("latitude" in changes or "longitude" in changes):
        changes.setdefault("latitude", row.get("latitude"))
        changes.setdefault("longitude", row.get("longitude"))
    if changes:
        _update(conn, table, pk_value, changes)
    return public_row(conn, table, pk_value), bool(changes)


# --- writes -------------------------------------------------------------------------------------

def create(conn: psycopg.Connection, table_name: str, body: Dict[str, Any], *, admin: bool = False
           ) -> Tuple[dict, bool]:
    """Add a row. Returns (row, replayed): replayed is True when an earlier identical write is returned."""
    table = TABLES[table_name]
    body = dict(body)
    idem = body.pop("idempotency_key", None)
    if idem is not None and (not isinstance(idem, str) or not idem.strip() or len(idem) > 200):
        raise ValueProblem("idempotency_key", "must be a non-empty string of at most 200 characters")
    id_prefix = body.pop("id_prefix", None) if table.name == "facilities" else None
    data = clean(table, body)
    _check_status(data, admin)

    if idem:
        _lock(conn, table.name, "idem", idem)
        existing = _by_idempotency_key(conn, table, idem)
        if existing is not None:
            return public_row(conn, table, existing), True
        data["idempotency_key"] = idem

    if table.name == "observations":
        return _create_observation(conn, table, data)
    if table.client_pk:
        pk_value = data[table.pk]
        _lock(conn, table.name, "pk", str(pk_value))
        if _raw(conn, table, pk_value) is not None:
            data.pop("idempotency_key", None)
            row, changed = _fill(conn, table, pk_value, data, admin=admin)
            return row, not changed
        _insert(conn, table, data)
        return public_row(conn, table, pk_value), False
    if table.name == "facilities":
        if id_prefix not in ("DC", "PWR"):
            raise ValueProblem("id_prefix", "must be DC (data center) or PWR (power plant)")
        if data["state"][:2] != data["country"]:
            raise ValueProblem("state", f"state {data['state']} is not in country {data['country']}")
        data["facility_id"] = db.allocate_facility_id(conn, id_prefix, data["state"])
    elif table.prefix:
        data[table.pk] = db.allocate_sequence_id(conn, table.name, table.pk, table.prefix)
    if table.name == "facility_names":
        _lock(conn, "facility_names", data["facility_id"], data["name"])
        r = conn.execute("SELECT id FROM facility_names WHERE facility_id = %s AND name = %s",
                         (data["facility_id"], data["name"])).fetchone()
        if r:
            return public_row(conn, table, r["id"]), True
    if table.name == "facility_links":
        rel = data.setdefault("relation", "serves")
        _lock(conn, "facility_links", data["from_facility_id"], data["to_facility_id"], rel)
        r = conn.execute("SELECT id FROM facility_links WHERE from_facility_id = %s AND to_facility_id = %s "
                         "AND relation = %s", (data["from_facility_id"], data["to_facility_id"], rel)).fetchone()
        if r:
            return public_row(conn, table, r["id"]), True
    pk_value = _insert(conn, table, data)
    return public_row(conn, table, pk_value), False


def _create_observation(conn: psycopg.Connection, table: Table, data: Dict[str, Any]) -> Tuple[dict, bool]:
    data.setdefault("date_found", dt.date.today())
    data.setdefault("review_status", "Needs review")
    if data.get("supersedes") and not _raw(conn, table, data["supersedes"]):
        raise ValueProblem("supersedes", f"observation {data['supersedes']} not found")
    # The natural key: the same fact from the same source is one row, even without an idempotency_key.
    nk = conn.execute("SELECT md5(coalesce(%(f)s, '') || chr(31) || %(field)s || chr(31) || coalesce(%(v)s, '') "
                      "|| chr(31) || %(u)s) AS nk",
                      {"f": data.get("facility_id"), "field": data["field"], "v": data.get("value"),
                       "u": data["source_url"]}).fetchone()["nk"]
    _lock(conn, "observations", "nk", nk)
    r = conn.execute("SELECT obs_id FROM observations WHERE natural_key = %s ORDER BY obs_id LIMIT 1", (nk,)).fetchone()
    if r:
        return public_row(conn, table, r["obs_id"]), True
    data["obs_id"] = db.allocate_sequence_id(conn, "observations", "obs_id", "OBS")
    _insert(conn, table, data)
    return public_row(conn, table, data["obs_id"]), False


def patch(conn: psycopg.Connection, table_name: str, pk_value: Any, body: Dict[str, Any], *,
          admin: bool = False) -> Tuple[dict, bool]:
    """Change an existing row. Ordinary keys fill blanks only; admin may overwrite (audited)."""
    table = TABLES[table_name]
    body = {k: v for k, v in body.items() if k != "idempotency_key"}
    if table.name == "observations":
        extra = set(body) - {"review_status", "notes"}
        if not admin or extra:
            raise Conflict("append_only", "observations are append-only; add a new observation with supersedes "
                           "(an admin may change only review_status and notes)", sorted(extra))
    data = clean(table, body, partial=True)
    if table.name == "facilities" and (("latitude" in data) != ("longitude" in data)):
        raise ValueProblem("latitude", "send latitude and longitude together")
    return _fill(conn, table, pk_value, data, admin=admin)


def create_claim_log(conn: psycopg.Connection, claim_id: str, body: Dict[str, Any]) -> Tuple[dict, bool]:
    if not conn.execute("SELECT 1 FROM claims WHERE claim_id = %s", (claim_id,)).fetchone():
        raise NotFound(f"claim {claim_id} not found")
    return create(conn, "claim_log", {**body, "claim_id": claim_id})


# --- reads --------------------------------------------------------------------------------------

def resolve_facility_id(conn: psycopg.Connection, fid: str) -> Optional[str]:
    r = conn.execute("""SELECT facility_id FROM facilities WHERE facility_id = %(id)s
                        UNION ALL SELECT facility_id FROM facility_aliases WHERE alias_id = %(id)s LIMIT 1""",
                     {"id": fid.upper()}).fetchone()
    return r["facility_id"] if r else None


def list_facilities(conn: psycopg.Connection, *, state: Optional[str] = None, type_: Optional[str] = None,
                    status: Optional[str] = None, operator: Optional[str] = None, q: Optional[str] = None,
                    near: Optional[Tuple[float, float]] = None, km: float = 25.0,
                    bbox: Optional[Tuple[float, float, float, float]] = None,
                    limit: int = 100, offset: int = 0) -> Tuple[List[dict], int]:
    where: List[sql.Composable] = [sql.SQL("TRUE")]
    params: Dict[str, Any] = {"limit": limit, "offset": offset}
    distance = sql.SQL("NULL::float")
    order = sql.SQL("f.facility_id")
    if state:
        where.append(sql.SQL("f.state = %(state)s"))
        params["state"] = state
    if type_:
        where.append(sql.SQL("f.facility_type ILIKE %(type)s"))
        params["type"] = type_
    if status:
        where.append(sql.SQL("f.status ILIKE %(status)s"))
        params["status"] = status
    if operator:
        where.append(sql.SQL("f.operator ILIKE %(operator)s"))
        params["operator"] = f"%{operator}%"
    if q:
        where.append(sql.SQL("""(f.name ILIKE %(q)s OR f.operator ILIKE %(q)s OR f.tenant ILIKE %(q)s
            OR f.facility_id = upper(%(qid)s)
            OR EXISTS (SELECT 1 FROM facility_names n WHERE n.facility_id = f.facility_id AND n.name ILIKE %(q)s))"""))
        params["q"], params["qid"] = f"%{q}%", q.strip()
    if near:
        params["lat"], params["lon"], params["m"] = near[0], near[1], km * 1000
        point = sql.SQL("ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)::geography")
        where.append(sql.SQL("ST_DWithin(f.geom, {}, %(m)s)").format(point))
        distance = sql.SQL("ST_Distance(f.geom, {}) / 1000.0").format(point)
        order = sql.SQL("ST_Distance(f.geom, {}), f.facility_id").format(point)
    if bbox:
        params["minlon"], params["minlat"], params["maxlon"], params["maxlat"] = bbox
        where.append(sql.SQL("f.geom && ST_MakeEnvelope(%(minlon)s, %(minlat)s, %(maxlon)s, %(maxlat)s, 4326)::geography"))
    query = sql.SQL("""SELECT {} || jsonb_build_object('distance_km', round(({})::numeric, 3)) AS row,
                              count(*) OVER () AS total
                       FROM facilities f WHERE {} ORDER BY {} LIMIT %(limit)s OFFSET %(offset)s""").format(
        FACILITY_JSON, distance, sql.SQL(" AND ").join(where), order)
    rows = conn.execute(query, params).fetchall()
    items = []
    for r in rows:
        item = r["row"]
        if item.get("distance_km") is None:
            item.pop("distance_km", None)
        items.append(item)
    return items, (rows[0]["total"] if rows else _count_beyond(conn, query, params, offset))


def _count_beyond(conn, query, params, offset) -> int:
    if offset == 0:
        return 0
    r = conn.execute(sql.SQL("SELECT count(*) AS n FROM ({}) s").format(query),
                     {**params, "limit": None, "offset": 0}).fetchone()
    return r["n"]


def facility_detail(conn: psycopg.Connection, fid: str) -> Optional[dict]:
    real = resolve_facility_id(conn, fid)
    if real is None:
        return None
    out = public_row(conn, TABLES["facilities"], real)
    out["names"] = [r["row"] for r in conn.execute(
        "SELECT to_jsonb(n) - 'idempotency_key' AS row FROM facility_names n WHERE facility_id = %s ORDER BY id",
        (real,)).fetchall()]
    out["links"] = [r["row"] for r in conn.execute(
        """SELECT to_jsonb(l) - 'idempotency_key' AS row FROM facility_links l
           WHERE from_facility_id = %(id)s OR to_facility_id = %(id)s ORDER BY id""", {"id": real}).fetchall()]
    out["aliases"] = [r["alias_id"] for r in conn.execute(
        "SELECT alias_id FROM facility_aliases WHERE facility_id = %s ORDER BY alias_id", (real,)).fetchall()]
    # Latest observation for each field: newest date found, skipping superseded and rejected rows.
    out["latest_observations"] = [r["row"] for r in conn.execute(
        """SELECT DISTINCT ON (o.field_group, o.field) to_jsonb(o) - 'idempotency_key' - 'natural_key' AS row
           FROM observations o
           WHERE o.facility_id = %s AND o.review_status IS DISTINCT FROM 'Rejected'
             AND NOT EXISTS (SELECT 1 FROM observations s WHERE s.supersedes = o.obs_id)
           ORDER BY o.field_group, o.field, o.date_found DESC, o.obs_id DESC""", (real,)).fetchall()]
    return out


def list_rows(conn: psycopg.Connection, table_name: str, filters: Dict[str, Any], *, limit: int, offset: int,
              since: Optional[dt.date] = None, since_column: Optional[str] = None) -> Tuple[List[dict], int]:
    table = TABLES[table_name]
    where: List[sql.Composable] = [sql.SQL("TRUE")]
    params: List[Any] = []
    for col, val in filters.items():
        if val is None:
            continue
        where.append(sql.SQL("{} = %s").format(sql.Identifier(col)))
        params.append(val)
    if since and since_column:
        where.append(sql.SQL("{} >= %s").format(sql.Identifier(since_column)))
        params.append(since)
    q = sql.SQL("""SELECT to_jsonb(t) - 'idempotency_key' - 'natural_key' AS row, count(*) OVER () AS total
                   FROM {} t WHERE {} ORDER BY {} LIMIT %s OFFSET %s""").format(
        sql.Identifier(table.name), sql.SQL(" AND ").join(where), sql.Identifier(table.pk))
    rows = conn.execute(q, params + [limit, offset]).fetchall()
    total = rows[0]["total"] if rows else 0
    return [r["row"] for r in rows], total


def claim_detail(conn: psycopg.Connection, claim_id: str) -> Optional[dict]:
    out = public_row(conn, TABLES["claims"], claim_id.upper())
    if out is None:
        return None
    out["log"] = [r["row"] for r in conn.execute(
        "SELECT to_jsonb(l) - 'idempotency_key' AS row FROM claim_log l WHERE claim_id = %s ORDER BY log_date, id",
        (out["claim_id"],)).fetchall()]
    return out


# --- keys ---------------------------------------------------------------------------------------

def save_key(conn: psycopg.Connection, key_id: str, key_hash: str, scopes: List[str], label: str) -> dict:
    return conn.execute("""INSERT INTO api_keys (key_id, key_hash, scopes, label) VALUES (%s, %s, %s, %s)
                           RETURNING key_id, scopes, label, created_at, revoked_at""",
                        (key_id, key_hash, scopes, label)).fetchone()


def load_key(conn: psycopg.Connection, key_id: str) -> Optional[dict]:
    return conn.execute("SELECT * FROM api_keys WHERE key_id = %s", (key_id,)).fetchone()


def list_keys(conn: psycopg.Connection) -> List[dict]:
    return conn.execute("SELECT key_id, scopes, label, created_at, revoked_at FROM api_keys ORDER BY created_at").fetchall()


def revoke_key(conn: psycopg.Connection, key_id: str) -> Optional[dict]:
    return conn.execute("""UPDATE api_keys SET revoked_at = coalesce(revoked_at, now()) WHERE key_id = %s
                           RETURNING key_id, scopes, label, created_at, revoked_at""", (key_id,)).fetchone()


def audit(conn: psycopg.Connection, key_id: str, action: str, table_name: str, row_id: Optional[str],
          after: Optional[dict] = None) -> None:
    conn.execute("INSERT INTO audit_log (key_id, action, table_name, row_id, after) VALUES (%s, %s, %s, %s, %s)",
                 (key_id, action, table_name, row_id, Jsonb(after) if after is not None else None))
