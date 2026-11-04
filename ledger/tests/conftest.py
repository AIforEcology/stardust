"""Tests run against a real PostgreSQL + PostGIS server (CI starts one as a service container).

Set LEDGER_TEST_DSN to a database the tests may use to create and drop scratch databases, e.g.
postgresql://postgres:postgres@localhost:5432/postgres. Without it, database tests are skipped.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Iterator, List

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import conninfo

from ledger import db
from ledger.api import create_app
from ledger.config import DEFAULT_MIGRATIONS, Settings

ADMIN_TOKEN = "test-admin-token"
FIXTURES = Path(__file__).parent / "fixtures" / "sheet"
DATA_TABLES = ("audit_log", "api_keys", "facility_links", "facility_names", "facility_aliases", "claim_log", "claims",
               "observations", "permit_checks", "water_ledger", "who_pays", "load_reality", "market_context",
               "corrections", "source_registry", "run_log", "facilities", "id_counters")


def _admin_dsn() -> str:
    dsn = os.environ.get("LEDGER_TEST_DSN")
    if not dsn:
        if os.environ.get("CI"):
            pytest.fail("LEDGER_TEST_DSN must be set in CI; database tests may not be skipped")
        pytest.skip("LEDGER_TEST_DSN not set (needs PostgreSQL with PostGIS)")
    return dsn


def scratch_db(name: str) -> str:
    """Create an empty database and return its DSN."""
    admin = _admin_dsn()
    with psycopg.connect(admin, autocommit=True) as c:
        c.execute(f'DROP DATABASE IF EXISTS "{name}"')
        c.execute(f'CREATE DATABASE "{name}"')
    return conninfo.make_conninfo(admin, dbname=name)


def drop_db(name: str) -> None:
    with psycopg.connect(_admin_dsn(), autocommit=True) as c:
        c.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture(scope="session")
def dsn() -> Iterator[str]:
    name = f"ledger_test_{uuid.uuid4().hex[:8]}"
    d = scratch_db(name)
    yield d
    drop_db(name)


@pytest.fixture(scope="session")
def pool(dsn):
    p = db.make_pool(dsn, 2, 20)
    with p.connection() as conn:
        db.migrate(conn, DEFAULT_MIGRATIONS)
    yield p
    p.close()


@pytest.fixture(autouse=True)
def _clean(request):
    if "pool" not in request.fixturenames:
        yield
        return
    p = request.getfixturevalue("pool")
    with p.connection() as conn:
        conn.execute(f"TRUNCATE {', '.join(DATA_TABLES)} RESTART IDENTITY CASCADE")
        conn.commit()
    yield


@pytest.fixture
def settings(dsn) -> Settings:
    return Settings(database_url=dsn, admin_token=ADMIN_TOKEN, reads_per_ip_per_minute=0, writes_per_ip_per_minute=0)


@pytest.fixture
def client(settings, pool) -> Iterator[TestClient]:
    with TestClient(create_app(settings, pool=pool)) as c:
        yield c


ADMIN = {"X-Ledger-Admin-Token": ADMIN_TOKEN}


def make_key(client: TestClient, scopes: List[str], label: str = "test") -> dict:
    r = client.post("/v1/admin/keys", json={"scopes": scopes, "label": label}, headers=ADMIN)
    assert r.status_code == 201, r.text
    return r.json()


def key_header(k: dict) -> dict:
    return {"X-Ledger-Key": k["key"]}


@pytest.fixture
def keys(client):
    """One key per scope, as headers."""
    return {s: key_header(make_key(client, [s], s)) for s in
            ("observations:write", "facilities:write", "tracker:write", "runlog:write")}


def add_facility(client: TestClient, headers: dict, **kw) -> dict:
    body = {"id_prefix": "DC", "name": "Test site", "state": "US-TX", "country": "US", **kw}
    r = client.post("/v1/facilities", json=body, headers=headers)
    assert r.status_code in (200, 201), r.text
    return r.json()
