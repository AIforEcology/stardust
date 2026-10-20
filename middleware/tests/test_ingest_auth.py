"""Keys on ingestion (STARDUST_INGEST_AUTH) and self-registered installs (POST /v1/installs)."""

from pathlib import Path

from fastapi.testclient import TestClient

from stardust_core.api import create_app
from stardust_core.config import DEFAULT_ESC, DEFAULT_METHODOLOGY, Settings

ADMIN = {"X-Stardust-Admin-Token": "admin-secret"}
ORG_A = "0a000000-0000-4000-8000-00000000000a"
ORG_B = "0b000000-0000-4000-8000-00000000000b"
USER_A1 = "a1000000-0000-4000-8000-0000000000a1"
USER_A2 = "a2000000-0000-4000-8000-0000000000a2"


def event(user=None, org=None):
    e = {"source_layer": "browser_ext", "provider": "anthropic", "model": "claude-test-opus",
         "timestamp": "2026-10-19T12:00:00Z", "tokens_in": 100, "tokens_out": 100}
    if user:
        e["user_id"] = user
    if org:
        e["org_id"] = org
    return e


def client(pricing: Path, ingest="required", reads="required", registration=True, per_ip=20) -> TestClient:
    s = Settings(DEFAULT_METHODOLOGY, DEFAULT_ESC, pricing, "https://example.org/donate", "admin-secret",
                 subscriber_auth=reads, ingest_auth=ingest, self_registration=registration,
                 registrations_per_ip_per_hour=per_ip)
    return TestClient(create_app(s))


def bearer(k):
    return {"Authorization": f"Bearer {k}"}


def test_off_accepts_events_without_a_key(pricing_file):
    with client(pricing_file, ingest="off", reads="off", registration=False) as c:
        assert c.post("/v1/events", json=event(USER_A1, ORG_A)).status_code == 200


def test_required_refuses_events_without_a_key(pricing_file):
    with client(pricing_file) as c:
        r = c.post("/v1/events", json=event(USER_A1, ORG_A))
        assert r.status_code == 401 and r.json()["reason"] == "unauthorized"
        assert c.post("/v1/events", json=event(), headers=bearer("sdk_bad_key")).status_code == 401


def test_install_key_writes_and_reads_only_its_own_data(pricing_file):
    with client(pricing_file) as c:
        a, b = c.post("/v1/installs").json(), c.post("/v1/installs").json()
        assert a["user_id"] != b["user_id"] and set(a["scopes"]) == {"reporting.read", "ingest.write"}
        # No ids in the event: the key fills them in.
        r = c.post("/v1/events", json=event(), headers=bearer(a["key"]))
        assert r.status_code == 200, r.text
        assert r.json()["user_id"] == a["user_id"] and r.json()["org_id"] == a["org_id"]
        c.post("/v1/events", json=event(), headers=bearer(b["key"]))
        assert c.get("/v1/summary", headers=bearer(a["key"])).json()["events"] == 1
        # Can't write as, or read, another install.
        assert c.post("/v1/events", json=event(user=b["user_id"]), headers=bearer(a["key"])).status_code == 403
        assert c.get(f"/v1/summary?user_id={b['user_id']}", headers=bearer(a["key"])).status_code == 403


def test_key_without_ingest_scope_is_refused(pricing_file):
    with client(pricing_file) as c:
        k = c.post("/v1/admin/keys", json={"org_id": ORG_A}, headers=ADMIN).json()["key"]  # reporting.read only
        r = c.post("/v1/events", json=event(), headers=bearer(k))
        assert r.status_code == 403 and r.json()["reason"] == "forbidden_scope"


def test_org_key_pins_org_and_refuses_other_orgs(pricing_file):
    with client(pricing_file) as c:
        k = c.post("/v1/admin/keys", json={"org_id": ORG_A, "scopes": ["ingest.write"]}, headers=ADMIN).json()["key"]
        r = c.post("/v1/events", json=event(user=USER_A2), headers=bearer(k))
        assert r.status_code == 200 and r.json()["org_id"] == ORG_A and r.json()["user_id"] == USER_A2
        assert c.post("/v1/events", json=event(org=ORG_B), headers=bearer(k)).status_code == 403


def test_optional_mode_accepts_both(pricing_file):
    with client(pricing_file, ingest="optional", reads="optional") as c:
        assert c.post("/v1/events", json=event(USER_A1, ORG_A)).status_code == 200
        k = c.post("/v1/installs").json()["key"]
        assert c.post("/v1/events", json=event(), headers=bearer(k)).status_code == 200


def test_revoked_install_key_stops_working(pricing_file):
    with client(pricing_file) as c:
        reg = c.post("/v1/installs").json()
        c.post(f"/v1/admin/keys/{reg['key_id']}/revoke", headers=ADMIN)
        assert c.post("/v1/events", json=event(), headers=bearer(reg["key"])).status_code == 401


def test_registration_is_off_by_default_and_rate_limited(pricing_file):
    with client(pricing_file, registration=False) as c:
        assert c.post("/v1/installs").status_code == 404
    with client(pricing_file, per_ip=2) as c:
        assert [c.post("/v1/installs").status_code for _ in range(3)] == [200, 200, 429]


def test_traces_need_a_key_when_required(pricing_file):
    with client(pricing_file) as c:
        r = c.post("/v1/traces", content=b"{}", headers={"content-type": "application/json"})
        assert r.status_code == 401
