"""The two ledger readers agree: the public API and the database (DbLedger) give the same footprint.

Runs in CI's ledger job, which has PostgreSQL + PostGIS (LEDGER_TEST_DSN) and Python 3.11+ for the
ledger package. Facilities are written through the real ledger API, so this also checks that the
footprint builder reads the API's actual response shapes.
"""

from __future__ import annotations

import datetime as dt
import os
import uuid

import pytest

ledger_pkg = pytest.importorskip("ledger", reason="needs the ledger package (Python 3.11+)")
if not os.environ.get("LEDGER_TEST_DSN"):
    pytest.skip("LEDGER_TEST_DSN not set (needs PostgreSQL with PostGIS)", allow_module_level=True)

import psycopg  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from ledger import db  # noqa: E402
from ledger.api import create_app  # noqa: E402
from ledger.config import DEFAULT_MIGRATIONS, Settings  # noqa: E402
from psycopg import conninfo  # noqa: E402

from dci_testdata import INTEL  # noqa: E402
from stardust_intel.allocations import load_allocations  # noqa: E402
from stardust_intel.footprint import build_footprint, comparable  # noqa: E402
from stardust_intel.grids import load_grids  # noqa: E402
from stardust_intel.ledger import ApiLedger, DbLedger  # noqa: E402

ADMIN = {"X-Ledger-Admin-Token": "intel-test"}


@pytest.fixture(scope="module")
def dsn():
    admin = os.environ["LEDGER_TEST_DSN"]
    name = f"intel_test_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(admin, autocommit=True) as c:
        c.execute(f'CREATE DATABASE "{name}"')
    yield conninfo.make_conninfo(admin, dbname=name)
    with psycopg.connect(admin, autocommit=True) as c:
        c.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture(scope="module")
def client(dsn):
    pool = db.make_pool(dsn, 1, 4)
    with pool.connection() as conn:
        db.migrate(conn, DEFAULT_MIGRATIONS)
    settings = Settings(database_url=dsn, admin_token="intel-test", reads_per_ip_per_minute=0,
                        writes_per_ip_per_minute=0)
    with TestClient(create_app(settings, pool=pool)) as c:
        yield c
    pool.close()


def _key(client, scope):
    r = client.post("/v1/admin/keys", json={"scopes": [scope], "label": scope}, headers=ADMIN)
    assert r.status_code == 201, r.text
    return {"X-Ledger-Key": r.json()["key"]}


def _add(client, headers, **kw):
    r = client.post("/v1/facilities", json={"country": "US", **kw}, headers=headers)
    assert r.status_code in (200, 201), r.text
    return r.json()["facility_id"]


@pytest.fixture(scope="module")
def seeded(client):
    fac, obs = _key(client, "facilities:write"), _key(client, "observations:write")
    _add(client, fac, id_prefix="DC", name="Project Rainier New Carlisle", state="US-IN", tenant="Anthropic",
         operator="Amazon", operational_mw=2200)
    tn = _add(client, fac, id_prefix="DC", name="Colossus 1", state="US-TN", tenant="Anthropic", operational_mw=300)
    pwr = _add(client, fac, id_prefix="PWR", name="Colossus 1 turbines", state="US-TN")
    r = client.post(f"/v1/facilities/{pwr}/links", json={"to_facility_id": tn, "link_status": "confirmed",
                                                         "source_url": "https://example.org/permit"}, headers=fac)
    assert r.status_code == 201, r.text
    r = client.post("/v1/observations", json={
        "facility_id": tn, "field_group": "Power: on-site", "field": "Gas turbines", "value": "15 turbines",
        "source_url": "https://example.org/air-permit", "date_found": "2026-10-30", "confidence": 0.9}, headers=obs)
    assert r.status_code == 201, r.text
    _add(client, fac, id_prefix="DC", name="Stargate Abilene", state="US-TX", tenant="OpenAI; Oracle")
    _add(client, fac, id_prefix="DC", name="Unallocated campus", state="US-VA", tenant="Anthropic")
    return tn, pwr


def test_api_and_database_agree(client, dsn, seeded):
    tn, pwr = seeded
    allocations = load_allocations(INTEL / "data" / "allocations.json")
    grids = load_grids(INTEL / "data" / "grids.json")
    core = __import__("json").loads((INTEL.parent / "schema" / "factors" / "methodology-v0.2.json").read_text())
    today = dt.date(2026, 11, 5)

    via_api = build_footprint(allocations, grids, core, ApiLedger("http://testserver", client=client), today=today)
    database = DbLedger(dsn)
    try:
        via_db = build_footprint(allocations, grids, core, database, today=today)
    finally:
        database.close()

    assert comparable(via_api) == comparable(via_db)
    assert via_db["ledger"]["source"] == "database"
    colossus = next(c for c in via_db["providers"]["anthropic"]["components"] if c["id"] == "colossus-1")
    f = colossus["facilities"][0]
    assert f["facility_id"] == tn
    assert f["power_plants"][0]["facility_id"] == pwr
    assert f["onsite_power"][0]["value"] == "15 turbines"
    assert f["operational_mw"] == 300
