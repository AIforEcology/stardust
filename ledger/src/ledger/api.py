"""The ledger's HTTP API, served at https://core.aifore.org/ledger/ (Caddy strips the /ledger prefix).

Reads are public and rate limited per IP. Writes need ``X-Ledger-Key: lk_<key_id>_<secret>`` with the
right scope. ``/v1/admin/*`` is blocked at Caddy and, as a second guard, refused here for any request
that came through a proxy (it carries X-Forwarded-For); use it from the server with
``docker compose exec ledger curl ... localhost:8090/v1/admin/...``.
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from typing import Any, Callable, Deque, Dict, List, Optional

import psycopg
from fastapi import Body, Depends, FastAPI, Header, Query, Request
from fastapi.responses import JSONResponse, Response
from psycopg_pool import ConnectionPool

from . import __version__, auth, db, importer, store
from .config import Settings
from .models import (ADMIN, FACILITIES_WRITE, OBSERVATIONS_WRITE, RUNLOG_WRITE, SCOPES, TABLES, TRACKER_WRITE,
                     ValueProblem, coerce)

log = logging.getLogger("ledger.api")

ATTRIBUTION = {
    "statement": "Data Center Resource Ledger by AI for Ecology (aifore.org). Every fact carries its own source.",
    "credits": [
        {"source": "Compute Atlas", "licence": "CC BY 4.0", "url": "https://www.compute-atlas.com",
         "credit": "Data from Compute Atlas (compute-atlas.com) by Edward Kubiak, licensed CC BY 4.0 "
                   "(https://creativecommons.org/licenses/by/4.0/)"},
        {"source": "Epoch AI", "licence": "CC BY 4.0", "url": "https://epoch.ai",
         "credit": "Data from Epoch AI, 'Frontier Data Centers' (epoch.ai), licensed CC BY 4.0"},
        {"source": "PeeringDB", "licence": "PeeringDB Acceptable Use Policy", "url": "https://www.peeringdb.com",
         "credit": "Facility data from PeeringDB (peeringdb.com)"},
    ],
    "openstreetmap": "If OpenStreetMap data is added: © OpenStreetMap contributors, ODbL 1.0. A public database "
                     "built from it must stay open (share-alike).",
    "details": "/ledger/v1/sources",
}

PROTECTED_SQLSTATES = {"LD001": "row_protected", "LD002": "append_only"}


class RateLimiter:
    """Sliding one-minute window per client IP, in memory (one ledger process)."""

    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self.hits: Dict[str, Deque[float]] = defaultdict(deque)

    def allow(self, ip: str) -> bool:
        if self.per_minute <= 0:
            return True
        now = time.monotonic()
        if len(self.hits) > 20000:  # forget idle clients
            for k in [k for k, v in self.hits.items() if not v or now - v[-1] > 60]:
                del self.hits[k]
        window = self.hits[ip]
        while window and now - window[0] > 60:
            window.popleft()
        if len(window) >= self.per_minute:
            return False
        window.append(now)
        return True


def _page(items: List[dict], total: int, limit: int, offset: int, **extra: Any) -> dict:
    nxt = offset + limit if offset + limit < total else None
    return {"items": items, "total": total, "limit": limit, "offset": offset, "next_offset": nxt,
            **extra, "attribution": ATTRIBUTION}


def _state(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    s = s.strip().upper()
    return f"US-{s}" if len(s) == 2 else s


def _floats(raw: str, n: int, name: str) -> List[float]:
    try:
        parts = [float(x) for x in raw.split(",")]
    except ValueError:
        parts = []
    if len(parts) != n:
        raise ValueProblem(name, f"expected {n} comma-separated numbers")
    return parts


def create_app(settings: Optional[Settings] = None, pool: Optional[ConnectionPool] = None) -> FastAPI:
    settings = settings or Settings.from_env()
    state: Dict[str, Any] = {"pool": pool}

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        owned = state["pool"] is None
        if owned:
            state["pool"] = db.make_pool(settings.database_url, settings.pool_min, settings.pool_max)
        with state["pool"].connection() as conn:
            db.migrate(conn, settings.migrations_dir)
        try:
            yield
        finally:
            if owned:
                state["pool"].close()

    app = FastAPI(title="Data Center Resource Ledger", version=__version__, lifespan=lifespan)
    reads, writes = RateLimiter(settings.reads_per_ip_per_minute), RateLimiter(settings.writes_per_ip_per_minute)

    def tx(admin_key_id: Optional[str] = None):
        return db.transaction(state["pool"], admin_key_id=admin_key_id)

    # --- errors ----------------------------------------------------------------------------------

    @app.exception_handler(auth.AuthError)
    async def _auth_error(_: Request, e: auth.AuthError) -> Response:
        return JSONResponse({"detail": e.message, "reason": e.reason}, status_code=e.status)

    @app.exception_handler(store.Conflict)
    async def _conflict(_: Request, e: store.Conflict) -> Response:
        return JSONResponse({"detail": e.message, "reason": e.reason, "fields": e.fields}, status_code=409)

    @app.exception_handler(store.NotFound)
    async def _not_found(_: Request, e: store.NotFound) -> Response:
        return JSONResponse({"detail": str(e), "reason": "not_found"}, status_code=404)

    @app.exception_handler(ValueProblem)
    async def _bad_value(_: Request, e: ValueProblem) -> Response:
        return JSONResponse({"detail": str(e), "reason": "invalid", "field": e.column}, status_code=422)

    @app.exception_handler(psycopg.Error)
    async def _db_error(_: Request, e: psycopg.Error) -> Response:
        code = e.sqlstate or ""
        msg = e.diag.message_primary if e.diag and e.diag.message_primary else "database error"
        if code in PROTECTED_SQLSTATES:
            return JSONResponse({"detail": msg, "reason": PROTECTED_SQLSTATES[code]}, status_code=409)
        if code == "23505":
            return JSONResponse({"detail": msg, "reason": "duplicate"}, status_code=409)
        if code.startswith("23") or code.startswith("22"):
            return JSONResponse({"detail": msg, "reason": "invalid"}, status_code=422)
        log.exception("database error")
        return JSONResponse({"detail": "database error", "reason": "server_error"}, status_code=500)

    @app.middleware("http")
    async def _rate_limit(request: Request, call_next: Callable):
        ip = request.client.host if request.client else "unknown"
        limiter = reads if request.method in ("GET", "HEAD") else writes
        if request.url.path != "/healthz" and not limiter.allow(ip):
            return JSONResponse({"detail": "too many requests from this address; try again in a minute",
                                 "reason": "rate_limited"}, status_code=429, headers={"Retry-After": "60"})
        return await call_next(request)

    # --- auth ------------------------------------------------------------------------------------

    def caller(x_ledger_key: Optional[str] = Header(default=None)) -> auth.Principal:
        if not x_ledger_key:
            raise auth.AuthError(401, "unauthorized", "a write key is required (X-Ledger-Key: lk_<key_id>_<secret>)")
        key_id = auth.split_key(x_ledger_key.strip())
        with tx() as conn:
            stored = store.load_key(conn, key_id) if key_id else None
        if stored is None or not auth.matches(x_ledger_key.strip(), stored["key_hash"]):
            raise auth.AuthError(401, "unauthorized", "invalid key")
        if stored["revoked_at"]:
            raise auth.AuthError(401, "key_revoked", "this key was revoked")
        return auth.Principal(stored["key_id"], frozenset(stored["scopes"]))

    def needs(scope: str):
        def dep(p: auth.Principal = Depends(caller)) -> auth.Principal:
            if not p.has(scope):
                raise auth.AuthError(403, "forbidden", f"this key lacks the {scope} scope")
            return p
        return dep

    def admin(request: Request, x_ledger_admin_token: Optional[str] = Header(default=None),
              x_ledger_key: Optional[str] = Header(default=None)) -> auth.Principal:
        # Caddy always adds X-Forwarded-For; admin calls come only from the server itself.
        if "x-forwarded-for" in request.headers or "forwarded" in request.headers:
            raise auth.AuthError(404, "not_found", "Not Found")
        if x_ledger_admin_token is not None:
            if not settings.admin_token:
                raise auth.AuthError(403, "forbidden", "LEDGER_ADMIN_TOKEN is not set")
            if not auth.token_matches(x_ledger_admin_token, settings.admin_token):
                raise auth.AuthError(401, "unauthorized", "invalid admin token")
            return auth.Principal(auth.ADMIN_TOKEN_KEY_ID, frozenset({ADMIN}))
        p = caller(x_ledger_key)
        if not p.has(ADMIN):
            raise auth.AuthError(403, "forbidden", "this key lacks the admin scope")
        return p

    def written(row: dict, replayed: bool) -> Response:
        return JSONResponse(_json(row) | {"idempotent_replay": replayed}, status_code=200 if replayed else 201)

    # --- public reads ------------------------------------------------------------------------------

    @app.get("/healthz")
    def healthz() -> Response:
        try:
            with tx() as conn:
                version = conn.execute("SELECT max(version) AS v FROM schema_migrations").fetchone()["v"]
                n = conn.execute("SELECT count(*) AS n FROM facilities").fetchone()["n"]
        except Exception:
            log.exception("health check failed")
            return JSONResponse({"ok": False, "database": "unavailable"}, status_code=503)
        return JSONResponse({"ok": True, "service": "ledger", "version": __version__, "schema_version": version,
                             "facilities": n})

    @app.get("/v1/facilities")
    def facilities(state: Optional[str] = None, type: Optional[str] = None, status: Optional[str] = None,
                   operator: Optional[str] = None, q: Optional[str] = None,
                   near: Optional[str] = Query(default=None, description="lat,lon"),
                   km: float = Query(default=25.0, gt=0, le=1000),
                   bbox: Optional[str] = Query(default=None, description="min_lon,min_lat,max_lon,max_lat"),
                   format: str = Query(default="json", pattern="^(json|geojson)$"),
                   limit: int = Query(default=100, ge=1, le=1000), offset: int = Query(default=0, ge=0)) -> Response:
        near_pt = _floats(near, 2, "near") if near else None
        if near_pt and not (-90 <= near_pt[0] <= 90 and -180 <= near_pt[1] <= 180):
            raise ValueProblem("near", "lat,lon out of range")
        box = _floats(bbox, 4, "bbox") if bbox else None
        with tx() as conn:
            items, total = store.list_facilities(conn, state=_state(state), type_=type, status=status,
                                                 operator=operator, q=q, near=tuple(near_pt) if near_pt else None,
                                                 km=km, bbox=tuple(box) if box else None, limit=limit, offset=offset)
        if format == "geojson":
            feats = [{"type": "Feature", "id": f["facility_id"],
                      "geometry": ({"type": "Point", "coordinates": [f["longitude"], f["latitude"]]}
                                   if f.get("latitude") is not None else None),
                      "properties": f} for f in items]
            return JSONResponse(_json({"type": "FeatureCollection", "features": feats, "total": total,
                                       "limit": limit, "offset": offset, "attribution": ATTRIBUTION}),
                                media_type="application/geo+json")
        return JSONResponse(_json(_page(items, total, limit, offset)))

    @app.get("/v1/facilities/{facility_id}")
    def facility(facility_id: str) -> Response:
        with tx() as conn:
            out = store.facility_detail(conn, facility_id)
        if out is None:
            raise store.NotFound(f"facility {facility_id} not found")
        return JSONResponse(_json(out | {"attribution": ATTRIBUTION}))

    @app.get("/v1/observations")
    def observations(facility_id: Optional[str] = None, field_group: Optional[str] = None,
                     since: Optional[dt.date] = None, limit: int = Query(default=100, ge=1, le=1000),
                     offset: int = Query(default=0, ge=0)) -> Response:
        with tx() as conn:
            fid = (store.resolve_facility_id(conn, facility_id) or facility_id.upper()) if facility_id else None
            items, total = store.list_rows(conn, "observations", {"facility_id": fid, "field_group": field_group},
                                           limit=limit, offset=offset, since=since, since_column="date_found")
        return JSONResponse(_json(_page(items, total, limit, offset)))

    def tracker_list(table: str, filters: Dict[str, Any], limit: int, offset: int, **extra: Any) -> Response:
        with tx() as conn:
            if filters.get("facility_id"):
                filters["facility_id"] = store.resolve_facility_id(conn, filters["facility_id"]) or filters["facility_id"]
            items, total = store.list_rows(conn, table, filters, limit=limit, offset=offset)
            if table == "load_reality":
                extra["market_context"] = store.list_rows(conn, "market_context", {}, limit=1000, offset=0)[0]
        return JSONResponse(_json(_page(items, total, limit, offset, **extra)))

    @app.get("/v1/claims")
    def claims(facility_id: Optional[str] = None, verdict: Optional[str] = None, claim_type: Optional[str] = None,
               limit: int = Query(default=100, ge=1, le=1000), offset: int = Query(default=0, ge=0)) -> Response:
        return tracker_list("claims", {"facility_id": facility_id, "verdict": verdict, "claim_type": claim_type},
                            limit, offset)

    @app.get("/v1/claims/{claim_id}")
    def claim(claim_id: str) -> Response:
        with tx() as conn:
            out = store.claim_detail(conn, claim_id)
        if out is None:
            raise store.NotFound(f"claim {claim_id} not found")
        return JSONResponse(_json(out | {"attribution": ATTRIBUTION}))

    @app.get("/v1/water")
    def water(facility_id: Optional[str] = None, state: Optional[str] = None,
              limit: int = Query(default=100, ge=1, le=1000), offset: int = Query(default=0, ge=0)) -> Response:
        return tracker_list("water_ledger", {"facility_id": facility_id, "state": _state(state)}, limit, offset)

    @app.get("/v1/who-pays")
    def who_pays(facility_id: Optional[str] = None, category: Optional[str] = None,
                 limit: int = Query(default=100, ge=1, le=1000), offset: int = Query(default=0, ge=0)) -> Response:
        return tracker_list("who_pays", {"facility_id": facility_id, "category": category}, limit, offset)

    @app.get("/v1/load-reality")
    def load_reality(facility_id: Optional[str] = None, tier: Optional[str] = None,
                     limit: int = Query(default=100, ge=1, le=1000), offset: int = Query(default=0, ge=0)) -> Response:
        return tracker_list("load_reality", {"facility_id": facility_id, "tier": tier}, limit, offset)

    @app.get("/v1/permit-checks")
    def permit_checks(facility_id: Optional[str] = None, status: Optional[str] = None,
                      limit: int = Query(default=100, ge=1, le=1000), offset: int = Query(default=0, ge=0)) -> Response:
        return tracker_list("permit_checks", {"facility_id": facility_id, "status": status}, limit, offset)

    @app.get("/v1/sources")
    def sources() -> Response:
        with tx() as conn:
            items, _ = store.list_rows(conn, "source_registry", {}, limit=1000, offset=0)
        return JSONResponse(_json({"items": items, "attribution": ATTRIBUTION}))

    # --- writes ----------------------------------------------------------------------------------

    def create(table: str, body: Dict[str, Any]) -> Response:
        with tx() as conn:
            row, replayed = store.create(conn, table, body)
        return written(row, replayed)

    def patch(table: str, pk: str, body: Dict[str, Any]) -> Response:
        with tx() as conn:
            row, changed = store.patch(conn, table, pk, body)
        return JSONResponse(_json(row) | {"changed": changed})

    @app.post("/v1/facilities", status_code=201)
    def add_facility(body: Dict[str, Any] = Body(...), _: auth.Principal = Depends(needs(FACILITIES_WRITE))) -> Response:
        return create("facilities", body)

    @app.patch("/v1/facilities/{facility_id}")
    def fill_facility(facility_id: str, body: Dict[str, Any] = Body(...),
                      _: auth.Principal = Depends(needs(FACILITIES_WRITE))) -> Response:
        with tx() as conn:
            real = store.resolve_facility_id(conn, facility_id)
        if real is None:
            raise store.NotFound(f"facility {facility_id} not found")
        return patch("facilities", real, body)

    @app.post("/v1/facilities/{facility_id}/names", status_code=201)
    def add_name(facility_id: str, body: Dict[str, Any] = Body(...),
                 _: auth.Principal = Depends(needs(FACILITIES_WRITE))) -> Response:
        return create("facility_names", {**body, "facility_id": facility_id})

    @app.post("/v1/facilities/{facility_id}/links", status_code=201)
    def add_link(facility_id: str, body: Dict[str, Any] = Body(...),
                 _: auth.Principal = Depends(needs(FACILITIES_WRITE))) -> Response:
        return create("facility_links", {**body, "from_facility_id": facility_id})

    @app.post("/v1/observations", status_code=201)
    def add_observation(body: Dict[str, Any] = Body(...),
                        _: auth.Principal = Depends(needs(OBSERVATIONS_WRITE))) -> Response:
        return create("observations", body)

    # No PUT, PATCH or DELETE routes exist for observations: they are append-only (405).

    @app.post("/v1/claims", status_code=201)
    def add_claim(body: Dict[str, Any] = Body(...), _: auth.Principal = Depends(needs(TRACKER_WRITE))) -> Response:
        return create("claims", body)

    @app.patch("/v1/claims/{claim_id}")
    def fill_claim(claim_id: str, body: Dict[str, Any] = Body(...),
                   _: auth.Principal = Depends(needs(TRACKER_WRITE))) -> Response:
        return patch("claims", claim_id.upper(), body)

    @app.post("/v1/claims/{claim_id}/log", status_code=201)
    def add_claim_log(claim_id: str, body: Dict[str, Any] = Body(...),
                      _: auth.Principal = Depends(needs(TRACKER_WRITE))) -> Response:
        with tx() as conn:
            row, replayed = store.create_claim_log(conn, claim_id.upper(), body)
        return written(row, replayed)

    for path, table in (("/v1/water", "water_ledger"), ("/v1/load-reality", "load_reality")):
        # One row per facility: POST creates it, or fills its blank fields.
        app.add_api_route(path, _poster(table, create, needs(TRACKER_WRITE)), methods=["POST"], status_code=201,
                          name=f"upsert_{table}")
    for path, table in (("/v1/who-pays", "who_pays"), ("/v1/permit-checks", "permit_checks"),
                        ("/v1/market-context", "market_context")):
        app.add_api_route(path, _poster(table, create, needs(TRACKER_WRITE)), methods=["POST"], status_code=201,
                          name=f"add_{table}")
    for path, table in (("/v1/who-pays/{row_id}", "who_pays"), ("/v1/permit-checks/{row_id}", "permit_checks")):
        app.add_api_route(path, _patcher(table, patch, needs(TRACKER_WRITE)), methods=["PATCH"], name=f"fill_{table}")

    @app.post("/v1/run-log", status_code=201)
    def add_run(body: Dict[str, Any] = Body(...), _: auth.Principal = Depends(needs(RUNLOG_WRITE))) -> Response:
        return create("run_log", body)

    # --- admin (server only) -----------------------------------------------------------------------

    @app.post("/v1/admin/keys", status_code=201)
    def create_key(body: Dict[str, Any] = Body(...), who: auth.Principal = Depends(admin)) -> Response:
        scopes = body.get("scopes")
        label = body.get("label")
        if not isinstance(scopes, list) or not scopes or any(s not in SCOPES for s in scopes):
            raise ValueProblem("scopes", f"a non-empty list drawn from {', '.join(SCOPES)}")
        if not isinstance(label, str) or not label.strip():
            raise ValueProblem("label", "required, e.g. the agent's name")
        key_id, key, key_hash = auth.new_key()
        with tx(who.key_id) as conn:
            rec = store.save_key(conn, key_id, key_hash, sorted(set(scopes)), label.strip())
            store.audit(conn, who.key_id, "create_key", "api_keys", key_id, {"scopes": rec["scopes"], "label": rec["label"]})
        return JSONResponse(_json(dict(rec) | {"key": key, "note": "shown once; only its hash is stored"}),
                            status_code=201)

    @app.get("/v1/admin/keys")
    def keys(_: auth.Principal = Depends(admin)) -> Response:
        with tx() as conn:
            return JSONResponse(_json({"keys": store.list_keys(conn)}))

    @app.post("/v1/admin/keys/{key_id}/revoke")
    def revoke(key_id: str, who: auth.Principal = Depends(admin)) -> Response:
        with tx(who.key_id) as conn:
            rec = store.revoke_key(conn, key_id)
        if rec is None:
            raise store.NotFound(f"key {key_id} not found")
        return JSONResponse(_json(rec))

    @app.patch("/v1/admin/rows/{table}/{row_id}")
    def admin_edit(table: str, row_id: str, body: Dict[str, Any] = Body(...),
                   who: auth.Principal = Depends(admin)) -> Response:
        """Change any field, including on Reviewed or Published rows. Every change goes to audit_log."""
        if table not in TABLES:
            raise store.NotFound(f"no table {table}")
        t = TABLES[table]
        pk: Any = int(row_id) if t.pk == "id" and row_id.isdigit() else row_id
        if t.pk == "facility_id":
            pk = coerce("facility_id", "facility_id", row_id)
        with tx(who.key_id) as conn:
            row, changed = store.patch(conn, table, pk, body, admin=True)
        return JSONResponse(_json(row) | {"changed": changed})

    @app.post("/v1/admin/import")
    def admin_import(body: Dict[str, Any] = Body(...), who: auth.Principal = Depends(admin)) -> Response:
        """Bulk upsert by ID, used by scripts/import_sheet.py. Body: {"table", "mode", "rows"}."""
        table, mode, rows = body.get("table"), body.get("mode", "upsert"), body.get("rows")
        if table not in TABLES and table != "facility_aliases":
            raise ValueProblem("table", "unknown table")
        if mode not in ("upsert", "fill") or not isinstance(rows, list) or len(rows) > 5000:
            raise ValueProblem("rows", "mode is upsert or fill; rows is a list of at most 5000 objects")
        with tx(who.key_id) as conn:
            results = importer.load(conn, table, mode, rows)
            db.sync_counters(conn)
            store.audit(conn, who.key_id, "import", table, None,
                        {"mode": mode, "rows": len(rows),
                         "rejected": sum(1 for r in results if r["status"] == "rejected")})
        return JSONResponse(_json({"results": results}))

    return app


def _poster(table: str, create: Callable, dep: Callable) -> Callable:
    def endpoint(body: Dict[str, Any] = Body(...), _: auth.Principal = Depends(dep)) -> Response:
        return create(table, body)
    return endpoint


def _patcher(table: str, patch: Callable, dep: Callable) -> Callable:
    def endpoint(row_id: str, body: Dict[str, Any] = Body(...), _: auth.Principal = Depends(dep)) -> Response:
        return patch(table, row_id.upper(), body)
    return endpoint


def _json(obj: Any) -> Any:
    from fastapi.encoders import jsonable_encoder
    return jsonable_encoder(obj)
