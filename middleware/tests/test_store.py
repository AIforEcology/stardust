import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from stardust_core.api import create_app
from stardust_core.config import DEFAULT_ESC, DEFAULT_METHODOLOGY, Settings
from stardust_core.store import SCHEMA_VERSION, Store, default_database_path

USER = "7b0c1f5e-8a9f-4a39-9d1c-2f6b1c0e9a11"
ORG = "0b5f6f0e-8a52-4a8e-9f0b-7c1d2e3f4a5b"


def event(**over):
    e = {"source_layer": "infra_agent", "provider": "anthropic", "model": "claude-test-opus", "region": "us-east-1",
         "timestamp": "2026-09-23T12:00:00Z", "tokens_in": 500, "tokens_out": 500, "user_id": USER}
    e.update(over)
    return e


def settings(pricing_file, db, **kw):
    return Settings(DEFAULT_METHODOLOGY, DEFAULT_ESC, pricing_file, "https://example.org/donate", kw.pop("admin", None),
                    database_path=str(db), **kw)


def test_events_survive_a_restart(pricing_file, tmp_path):
    db = tmp_path / "core.db"
    with TestClient(create_app(settings(pricing_file, db))) as c:
        first = c.post("/v1/events", json=event(event_id=str(uuid.uuid4()))).json()
        c.post("/v1/events", json=event(tokens_in=100, tokens_out=50))
        before = c.get("/v1/summary").json()

    with TestClient(create_app(settings(pricing_file, db))) as c:  # a fresh Core on the same file
        after = c.get("/v1/summary").json()
        assert after == before
        assert after["events"] == 2
        # De-duplication survives the restart: a retry of the first event isn't counted again.
        again = c.post("/v1/events", json=event(event_id=first["event_id"])).json()
        assert again["job_sji"] == first["job_sji"]
        assert c.get("/v1/summary").json()["events"] == 2
        assert [e["event_id"] for e in c.get("/v1/events", params={"limit": 1}).json()] != []


def test_concurrent_retries_store_one_event(pricing_file, tmp_path):
    with TestClient(create_app(settings(pricing_file, tmp_path / "core.db"))) as c:
        e = event(event_id=str(uuid.uuid4()))
        results = []
        threads = [threading.Thread(target=lambda: results.append(c.post("/v1/events", json=e).json()["job_sji"])) for _ in range(12)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        assert len(set(results)) == 1  # every caller got the same enriched event
        assert c.get("/v1/summary").json()["events"] == 1


def test_summary_filters_and_daily_totals(pricing_file, tmp_path):
    with TestClient(create_app(settings(pricing_file, tmp_path / "core.db"))) as c:
        c.post("/v1/events", json=event(timestamp="2026-09-20T08:00:00Z", org_id=ORG))
        c.post("/v1/events", json=event(timestamp="2026-09-21T09:00:00-07:00", org_id=ORG))  # 16:00 UTC
        c.post("/v1/events", json=event(timestamp="2026-09-21T23:30:00-07:00"))  # 06:30 UTC on the 22nd
        c.post("/v1/events", json=event(user_id=str(uuid.uuid4()), model="unknown"))

        assert c.get("/v1/summary").json()["events"] == 4
        assert c.get("/v1/summary", params={"user_id": USER}).json()["events"] == 3
        assert c.get("/v1/summary", params={"org_id": ORG}).json()["events"] == 2
        window = c.get("/v1/summary", params={"since": "2026-09-21T00:00:00Z", "until": "2026-09-22T00:00:00Z"}).json()
        assert window["events"] == 1  # time filters compare in UTC

        s = c.get("/v1/summary").json()
        assert s["cost_unknown_events"] == 1
        assert s["indicator_code"] == "C3-S"

        days = c.get("/v1/summary/daily", params={"user_id": USER}).json()["days"]
        assert [(d["day"], d["events"]) for d in days] == [("2026-09-20", 1), ("2026-09-21", 1), ("2026-09-22", 1)]


def test_delete_user_events_and_retention(pricing_file, tmp_path):
    db = tmp_path / "core.db"
    old = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    new = datetime.now(timezone.utc).isoformat()
    other = str(uuid.uuid4())
    with TestClient(create_app(settings(pricing_file, db, admin="s3cret"))) as c:
        c.post("/v1/events", json=event(timestamp=old))
        c.post("/v1/events", json=event(timestamp=new))
        c.post("/v1/events", json=event(timestamp=new, user_id=other))
        assert c.delete(f"/v1/admin/users/{other}/events").status_code == 401
        r = c.delete(f"/v1/admin/users/{other}/events", headers={"X-Stardust-Admin-Token": "s3cret"})
        assert r.json() == {"deleted": 1}
        assert c.get("/v1/summary").json()["events"] == 2

    # Restart with a 30-day retention: the 40-day-old event is purged at startup.
    with TestClient(create_app(settings(pricing_file, db, retention_days=30))) as c:
        assert c.get("/v1/summary").json()["events"] == 1


def test_broker_state_survives_a_restart(pricing_file, tmp_path):
    pytest.importorskip("stardust_provider")
    from stardust_provider.examples.mock_reforestation import MockReforestationProvider
    from stardust_provider.server import create_app as provider_app

    db, admin = tmp_path / "core.db", {"X-Stardust-Admin-Token": "s3cret"}
    provider = MockReforestationProvider()  # keep one instance so its order records persist too

    def core():
        http = httpx.AsyncClient(transport=httpx.ASGITransport(app=provider_app(provider)))
        return TestClient(create_app(settings(pricing_file, db, admin="s3cret"), http=http))

    with core() as c:
        c.post("/v1/admin/providers", json={"base_url": "http://mock"}, headers=admin)
        c.post("/v1/admin/providers/mock-reforestation/approve", json={"tier": "emerging"}, headers=admin)
        quotes = c.post("/v1/remediation/quotes", json={"co2e_g": 500_000}).json()["quotes"]
        spare = c.post("/v1/remediation/quotes", json={"co2e_g": 1_000}).json()["quotes"][0]
        order = c.post("/v1/remediation/orders", json={"quote_id": quotes[0]["quote_id"], "subscriber_id": "app"}).json()

    with core() as c:
        # The vetted provider, the order and the unused quote are all still there.
        assert [p["provider_id"] for p in c.get("/v1/providers").json()["providers"]] == ["mock-reforestation"]
        status = c.get(f"/v1/remediation/orders/{order['remediation_order_id']}").json()
        assert status["fulfillment_status"] == "fulfilled"
        assert status["certificate_ref"].startswith("MOCK-ATTESTATION-")
        assert c.post("/v1/remediation/orders", json={"quote_id": quotes[0]["quote_id"], "subscriber_id": "app"}).status_code == 400
        assert c.post("/v1/remediation/orders", json={"quote_id": spare["quote_id"], "subscriber_id": "app"}).status_code == 200


def test_quote_is_taken_once_under_concurrency(tmp_path):
    store = Store(tmp_path / "core.db")
    qid = uuid.uuid4()
    store.save_quote(qid, datetime.now(timezone.utc) + timedelta(minutes=5), {"quote_id": str(qid)})
    winners = []
    threads = [threading.Thread(target=lambda: winners.append(store.take_quote(qid))) for _ in range(10)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sum(w is not None for w in winners) == 1


def test_expired_quotes_are_purged(tmp_path):
    store = Store(tmp_path / "core.db")
    now = datetime.now(timezone.utc)
    store.save_quote(uuid.uuid4(), now - timedelta(minutes=1), {})
    store.save_quote(uuid.uuid4(), now + timedelta(minutes=10), {})
    assert store.purge_expired_quotes(now) == 1
    assert store.stats()["rows"]["quotes"] == 1


def test_file_database_uses_wal_and_records_schema_version(tmp_path):
    db = tmp_path / "core.db"
    Store(db).close()
    conn = sqlite3.connect(db)
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_refuses_a_newer_schema(tmp_path):
    db = tmp_path / "core.db"
    Store(db).close()
    conn = sqlite3.connect(db)
    conn.execute(f"PRAGMA user_version={SCHEMA_VERSION + 1}")
    conn.close()
    with pytest.raises(RuntimeError, match="schema"):
        Store(db)


def test_default_path_is_outside_the_repo():
    path = default_database_path()
    assert path.name == "core.db" and path.parent.name == "stardust"
    assert "stardust/middleware" not in str(path)
