"""Subscriber API keys and tenant isolation (auth.py, spec §23.6)."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from stardust_core import auth
from stardust_core.api import create_app
from stardust_core.config import DEFAULT_ESC, DEFAULT_METHODOLOGY, Settings

ADMIN = {"X-Stardust-Admin-Token": "admin-secret"}
ORG_A = "0a000000-0000-4000-8000-00000000000a"
ORG_B = "0b000000-0000-4000-8000-00000000000b"
USER_A1 = "a1000000-0000-4000-8000-0000000000a1"
USER_A2 = "a2000000-0000-4000-8000-0000000000a2"
USER_B1 = "b1000000-0000-4000-8000-0000000000b1"


def event(user, org, tokens=100):
    return {"source_layer": "infra_agent", "provider": "anthropic", "model": "claude-test-opus",
            "timestamp": "2026-09-23T12:00:00Z", "tokens_in": tokens, "tokens_out": tokens,
            "user_id": user, "org_id": org}


def make_client(pricing: Path, mode: str) -> TestClient:
    s = Settings(DEFAULT_METHODOLOGY, DEFAULT_ESC, pricing, "https://example.org/donate", "admin-secret",
                 subscriber_auth=mode)
    return TestClient(create_app(s))


@pytest.fixture(params=["optional", "required"])
def client(request, pricing_file):
    with make_client(pricing_file, request.param) as c:
        # Two orgs; org A has two users. Ingestion needs no key in M1.
        for user, org in ((USER_A1, ORG_A), (USER_A1, ORG_A), (USER_A2, ORG_A), (USER_B1, ORG_B)):
            assert c.post("/v1/events", json=event(user, org)).status_code == 200
        c.mode = request.param
        yield c


def key(c, **body):
    r = c.post("/v1/admin/keys", json={"org_id": ORG_A, **body}, headers=ADMIN)
    assert r.status_code == 200, r.text
    return r.json()


def bearer(k):
    return {"Authorization": f"Bearer {k['key']}"}


# --- modes ---------------------------------------------------------------------------------------


def test_off_ignores_keys_and_serves_as_before(pricing_file):
    with make_client(pricing_file, "off") as c:
        c.post("/v1/events", json=event(USER_A1, ORG_A))
        c.post("/v1/events", json=event(USER_B1, ORG_B))
        r = c.get("/v1/summary", headers={"Authorization": "Bearer sdk_nonsense_value"})
        assert r.status_code == 200 and r.json()["events"] == 2
        assert c.get("/healthz").json()["subscriber_auth"] == "off"


def test_without_a_key(client):
    r = client.get("/v1/summary")
    if client.mode == "optional":
        assert r.status_code == 200 and r.json()["events"] == 4  # unchanged for existing clients
    else:
        assert r.status_code == 401
        assert r.json()["reason"] == "unauthorized"
        assert r.headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.parametrize("header", ["Bearer sdk_nope_nope", "Bearer not-a-key", "Basic abc"])
def test_bad_keys_are_refused(client, header):
    r = client.get("/v1/summary", headers={"Authorization": header})
    if header.startswith("Basic") and client.mode == "optional":
        assert r.status_code == 200  # not a bearer key: treated as no key
    else:
        assert r.status_code == 401 and r.json()["reason"] == "unauthorized"


def test_a_tampered_key_is_refused(client):
    k = key(client)
    tampered = k["key"][:-1] + ("A" if k["key"][-1] != "A" else "B")
    assert client.get("/v1/summary", headers={"Authorization": f"Bearer {tampered}"}).status_code == 401


# --- tenant isolation -----------------------------------------------------------------------------


def test_org_key_sees_only_its_org(client):
    h = bearer(key(client))
    assert client.get("/v1/summary", headers=h).json()["events"] == 3
    assert client.get("/v1/summary", headers=h, params={"org_id": ORG_A}).json()["events"] == 3
    assert client.get("/v1/summary", headers=h, params={"user_id": USER_A2}).json()["events"] == 1
    # A user in another org gives nothing, not that user's data.
    assert client.get("/v1/summary", headers=h, params={"user_id": USER_B1}).json()["events"] == 0
    assert len(client.get("/v1/events", headers=h).json()) == 3
    assert sum(d["events"] for d in client.get("/v1/summary/daily", headers=h).json()["days"]) == 3


@pytest.mark.parametrize("path", ["/v1/summary", "/v1/summary/daily"])
def test_asking_for_another_org_is_forbidden(client, path):
    r = client.get(path, headers=bearer(key(client)), params={"org_id": ORG_B})
    assert r.status_code == 403 and r.json()["reason"] == "forbidden_scope"


def test_user_key_sees_only_its_user(client):
    h = bearer(key(client, user_id=USER_A1))
    assert client.get("/v1/summary", headers=h).json()["events"] == 2
    assert {e["user_id"] for e in client.get("/v1/events", headers=h).json()} == {USER_A1}
    r = client.get("/v1/summary", headers=h, params={"user_id": USER_A2})
    assert r.status_code == 403 and r.json()["reason"] == "forbidden_scope"


def test_key_without_reporting_scope_is_forbidden(client):
    h = bearer(key(client, scopes=["provider.read"]))
    r = client.get("/v1/summary", headers=h)
    assert r.status_code == 403 and r.json()["reason"] == "forbidden_scope"


def test_revoked_key_stops_working(client):
    k = key(client)
    assert client.get("/v1/summary", headers=bearer(k)).status_code == 200
    r = client.post(f"/v1/admin/keys/{k['key_id']}/revoke", headers=ADMIN)
    assert r.status_code == 200 and r.json()["revoked_at"]
    assert client.get("/v1/summary", headers=bearer(k)).status_code == 401
    # Revoking again keeps the first time.
    assert client.post(f"/v1/admin/keys/{k['key_id']}/revoke", headers=ADMIN).json()["revoked_at"] == r.json()["revoked_at"]


# --- key administration ---------------------------------------------------------------------------


def test_key_admin_needs_the_admin_token(client):
    assert client.post("/v1/admin/keys", json={"org_id": ORG_A}).status_code == 401
    assert client.get("/v1/admin/keys").status_code == 401


def test_created_key_is_shown_once_and_stored_hashed(client):
    k = key(client, label="reporting for org A")
    assert k["key"].startswith("sdk_" + k["key_id"] + "_")
    assert k["scopes"] == ["reporting.read"] and k["user_id"] is None and k["revoked_at"] is None
    assert "key_hash" not in k

    listed = client.get("/v1/admin/keys", headers=ADMIN, params={"org_id": ORG_A}).json()["keys"]
    assert [x["key_id"] for x in listed] == [k["key_id"]]
    assert "key" not in listed[0] and "key_hash" not in listed[0]
    assert client.get("/v1/admin/keys", headers=ADMIN, params={"org_id": ORG_B}).json()["keys"] == []

    stored = client.app.state.store.load_api_key(k["key_id"])
    assert stored["key_hash"] == auth.hash_key(k["key"]) != k["key"]


def test_unknown_scopes_are_rejected(client):
    r = client.post("/v1/admin/keys", json={"org_id": ORG_A, "scopes": ["reporting.write"]}, headers=ADMIN)
    assert r.status_code == 400
    r = client.post("/v1/admin/keys", json={"org_id": ORG_A, "scopes": []}, headers=ADMIN)
    assert r.status_code == 400


def test_revoking_an_unknown_key_is_404(client):
    assert client.post("/v1/admin/keys/nope/revoke", headers=ADMIN).status_code == 404


def test_healthz_reports_the_mode(client):
    assert client.get("/healthz").json()["subscriber_auth"] == client.mode


# --- unit -----------------------------------------------------------------------------------------


def test_parse_auth_mode():
    assert auth.parse_auth_mode("") == "off"
    assert auth.parse_auth_mode(" Required ") == "required"
    with pytest.raises(ValueError):
        auth.parse_auth_mode("on")


def test_split_key():
    assert auth.split_key("sdk_abc_secret") == "abc"
    for bad in ("abc_secret", "sdk_", "sdk_abc", "sdk__secret", "sdk_abc_"):
        assert auth.split_key(bad) is None
