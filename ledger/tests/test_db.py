"""Migrations, ID allocation and the database's own guards."""

import threading
import uuid

import psycopg
import pytest

from conftest import drop_db, scratch_db
from ledger import db, store
from ledger.config import DEFAULT_MIGRATIONS


def test_migrations_apply_cleanly_to_an_empty_database():
    name = f"ledger_mig_{uuid.uuid4().hex[:8]}"
    dsn = scratch_db(name)
    try:
        with psycopg.connect(dsn) as conn:
            applied = db.migrate(conn, DEFAULT_MIGRATIONS)
            assert applied == [n for _, n, _ in db.migration_files(DEFAULT_MIGRATIONS)]
            assert db.migrate(conn, DEFAULT_MIGRATIONS) == []  # second run applies nothing
            versions = [r[0] for r in conn.execute("SELECT version FROM schema_migrations ORDER BY 1")]
            assert versions == [v for v, _, _ in db.migration_files(DEFAULT_MIGRATIONS)]
            assert conn.execute("SELECT PostGIS_Version()").fetchone()[0]
    finally:
        drop_db(name)


def test_next_id_is_unique_and_gapless_under_concurrency(pool):
    ids, errors = [], []
    barrier = threading.Barrier(10)

    def worker(i):
        try:
            barrier.wait()
            with db.transaction(pool) as conn:
                row, _ = store.create(conn, "facilities", {"id_prefix": "DC", "name": f"Site {i}", "state": "US-TX",
                                                           "country": "US"})
            ids.append(row["facility_id"])
        except Exception as e:  # pragma: no cover - reported below
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors
    assert sorted(ids) == [f"DC-US-TX-{n:04d}" for n in range(1, 11)]


def test_rolled_back_write_gives_its_number_back(pool):
    with pytest.raises(RuntimeError):
        with db.transaction(pool) as conn:
            store.create(conn, "facilities", {"id_prefix": "DC", "name": "x", "state": "US-OH", "country": "US"})
            raise RuntimeError("abort")
    with db.transaction(pool) as conn:
        row, _ = store.create(conn, "facilities", {"id_prefix": "DC", "name": "y", "state": "US-OH", "country": "US"})
    assert row["facility_id"] == "DC-US-OH-0001"


def test_counters_are_separate_per_prefix_and_state(pool):
    with db.transaction(pool) as conn:
        a = store.create(conn, "facilities", {"id_prefix": "DC", "name": "a", "state": "US-OH", "country": "US"})[0]
        b = store.create(conn, "facilities", {"id_prefix": "PWR", "name": "b", "state": "US-OH", "country": "US"})[0]
        c = store.create(conn, "facilities", {"id_prefix": "DC", "name": "c", "state": "US-TX", "country": "US"})[0]
    assert (a["facility_id"], b["facility_id"], c["facility_id"]) == ("DC-US-OH-0001", "PWR-US-OH-0001", "DC-US-TX-0001")


def _observation(conn):
    store.create(conn, "facilities", {"id_prefix": "DC", "name": "a", "state": "US-TX", "country": "US"})
    return store.create(conn, "observations", {"facility_id": "DC-US-TX-0001", "field_group": "Status & size",
                                               "field": "Capacity", "value": "100", "unit": "MW",
                                               "source_url": "https://example.org/a"})[0]


def test_database_refuses_updating_or_deleting_observations(pool):
    with db.transaction(pool) as conn:
        obs = _observation(conn)
    for stmt in ("UPDATE observations SET value = '200'", "DELETE FROM observations"):
        with pytest.raises(psycopg.Error) as e:
            with db.transaction(pool) as conn:
                conn.execute(stmt)
        assert e.value.sqlstate == "LD002"
    # Even an admin may change only review_status and notes, and never delete.
    with pytest.raises(psycopg.Error):
        with db.transaction(pool, admin_key_id="t") as conn:
            conn.execute("UPDATE observations SET value = '200'")
    with pytest.raises(psycopg.Error):
        with db.transaction(pool, admin_key_id="t") as conn:
            conn.execute("DELETE FROM observations")
    with db.transaction(pool, admin_key_id="t") as conn:
        conn.execute("UPDATE observations SET review_status = 'Verified' WHERE obs_id = %s", (obs["obs_id"],))
        assert conn.execute("SELECT count(*) AS n FROM audit_log WHERE table_name = 'observations'").fetchone()["n"] == 1


def test_database_protects_reviewed_rows(pool):
    with db.transaction(pool) as conn:
        store.create(conn, "facilities", {"id_prefix": "DC", "name": "a", "state": "US-TX", "country": "US"})
    with db.transaction(pool, admin_key_id="t") as conn:
        conn.execute("UPDATE facilities SET review_status = 'Reviewed'")
    with pytest.raises(psycopg.Error) as e:
        with db.transaction(pool) as conn:
            conn.execute("UPDATE facilities SET notes = 'x'")
    assert e.value.sqlstate == "LD001"
    with pytest.raises(psycopg.Error) as e:
        with db.transaction(pool) as conn:
            conn.execute("DELETE FROM facilities")
    assert e.value.sqlstate == "LD001"


def test_generated_columns(pool):
    with db.transaction(pool) as conn:
        store.create(conn, "facilities", {"id_prefix": "DC", "name": "a", "state": "US-TX", "country": "US"})
        lr = store.create(conn, "load_reality", {
            "facility_id": "DC-US-TX-0001", "stated_load_mw": "1050", "land_control": 2, "local_approvals": 2,
            "power_secured": 2, "equipment_financing": 1, "construction": 2, "sponsor_tenant": 2})[0]
        partial = store.create(conn, "facilities", {"id_prefix": "DC", "name": "b", "state": "US-TX", "country": "US"})[0]
        lr2 = store.create(conn, "load_reality", {"facility_id": partial["facility_id"], "land_control": 2})[0]
        w = store.create(conn, "water_ledger", {"facility_id": "DC-US-TX-0001", "cooling_type": "Air / dry",
                                                "supply_source_type": "Unknown", "discharge": "1.58 MGD cap"})[0]
        pva = store.create(conn, "permit_checks", {"metric": "turbines", "limit_value": "0", "actual_value": "58"})[0]
    assert (lr["deliverability_score"], lr["tier"], float(lr["score_weighted_mw"])) == (92, "Built or near-certain", 966.0)
    assert lr2["deliverability_score"] is None and lr2["tier"] is None  # unknown is not zero
    assert w["completeness"] == 2
    assert float(pva["gap"]) == 58
