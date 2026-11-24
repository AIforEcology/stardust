"""veritree driver: a fake veritree API on httpx.MockTransport. No test calls the real API."""

import json
from typing import Any, Dict, List

import httpx
import pytest
from fastapi.testclient import TestClient

from stardust_provider.interface import FulfillRequest
from stardust_provider.providers.veritree import (
    INVOICE_PREFIX,
    VeritreeClient,
    VeritreeConfig,
    VeritreeError,
    VeritreeProvider,
    create_veritree_app,
)

PASSWORD = "not-a-real-password"
LIVE_ENV = {
    "VERITREE_MODE": "live",
    "VERITREE_USERNAME": "ops@example.org",
    "VERITREE_PASSWORD": PASSWORD,
    "VERITREE_ORG_ID": "42",
    "VERITREE_COUNTERPARTY_ID": "42",
    "VERITREE_PLANTING_SITE_ID": "7",
    "VERITREE_PRICE_USD_PER_TREE": "1.50",
    "VERITREE_T_CO2E_PER_TREE": "0.25",  # test figure only
}
DRY_ENV = {k: v for k, v in LIVE_ENV.items() if k in (
    "VERITREE_PLANTING_SITE_ID", "VERITREE_PRICE_USD_PER_TREE", "VERITREE_T_CO2E_PER_TREE")}


class FakeVeritree:
    """Just enough of api.veritree.com: token, planting site, tree orders (enveloped as in API v2+)."""

    def __init__(self) -> None:
        self.orders: Dict[int, Dict[str, Any]] = {}
        self.created: List[Dict[str, Any]] = []
        self.logins = 0
        self.valid_tokens = set()
        self.requests: List[httpx.Request] = []

    def wrap(self, data: Any, code: int = 200) -> httpx.Response:
        return httpx.Response(code, json={"_result": {"http_code": code}, "data": data})

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == "/api/token":
            assert b"multipart/form-data" in request.headers["content-type"].encode()
            if PASSWORD.encode() not in request.content:
                return httpx.Response(401, json={"message": "Unauthenticated."})
            self.logins += 1
            token = f"tok-{self.logins}"
            self.valid_tokens.add(token)
            return httpx.Response(200, json={"token_type": "Bearer", "expires_in": 14400,
                                             "access_token": token, "refresh_token": "r"})
        if request.headers.get("authorization", "").removeprefix("Bearer ") not in self.valid_tokens:
            return httpx.Response(401, json={"message": "Unauthenticated."})
        assert request.url.params["org_id"] == "42" and request.url.params["org_type"] == "sponsorAccount"
        if path == "/api/planting-sites/7":
            return self.wrap({"id": 7, "name": "Test site", "plus_code": "5HM6QQR5+X4", "forest_type_id": 3})
        if path == "/api/tree-orders" and request.method == "GET":
            inv = request.url.params.get("invoice_id")
            return self.wrap([o for o in self.orders.values() if o["invoice_id"] == inv])
        if path == "/api/tree-orders" and request.method == "POST":
            body = json.loads(request.content)
            self.created.append(body)
            oid = len(self.orders) + 100
            order = {**body, "id": oid, "public_id": f"pub-{oid}", "status": 1, "paid": 0,
                     "allocated_trees": "0", "outstanding_trees": body["tree_amount"]}
            self.orders[oid] = order
            return self.wrap(order, 201)
        if path.startswith("/api/tree-orders/"):
            key = path.rsplit("/", 1)[1]
            for o in self.orders.values():
                if key in (str(o["id"]), o["public_id"]):
                    return httpx.Response(200, json=o)  # the detail endpoint's documented example is bare
            return httpx.Response(404)
        return httpx.Response(404)

    def set_status(self, status: int, allocated: str = "0") -> None:
        for o in self.orders.values():
            o["status"], o["allocated_trees"] = status, allocated


@pytest.fixture
def fake():
    return FakeVeritree()


@pytest.fixture
def live(fake):
    return TestClient(create_veritree_app(LIVE_ENV, transport=httpx.MockTransport(fake.handler)))


def order_for(client, co2e_g=1_000_000, order_id="o-1"):
    q = client.post("/quote", json={"co2e_g": co2e_g}).json()
    return {"order_id": order_id, "project_id": q["project_id"], "co2e_g": co2e_g, "price_usd": q["price_usd"]}


# --- configuration --------------------------------------------------------------------------------------

def test_config_requires_price_co2e_site_and_live_credentials():
    with pytest.raises(VeritreeError) as e:
        VeritreeConfig.from_env({})
    assert "PRICE_USD_PER_TREE" in str(e.value) and "T_CO2E_PER_TREE" in str(e.value)
    assert "PLANTING_SITE_ID" in str(e.value)
    with pytest.raises(VeritreeError, match="live mode needs VERITREE_PASSWORD"):
        VeritreeConfig.from_env({**LIVE_ENV, "VERITREE_PASSWORD": ""})
    with pytest.raises(VeritreeError, match="dry_run or live"):
        VeritreeConfig.from_env({**DRY_ENV, "VERITREE_MODE": "prod"})
    with pytest.raises(VeritreeError, match="not a valid number"):
        VeritreeConfig.from_env({**DRY_ENV, "VERITREE_PRICE_USD_PER_TREE": "cheap"})
    assert VeritreeConfig.from_env(DRY_ENV).mode == "dry_run"  # the default never places real orders


def test_password_from_secret_file_and_never_in_repr(tmp_path):
    secret = tmp_path / "veritree_password"
    secret.write_text(PASSWORD + "\n")
    env = {**LIVE_ENV, "VERITREE_PASSWORD_FILE": str(secret)}
    del env["VERITREE_PASSWORD"]
    cfg = VeritreeConfig.from_env(env)
    assert cfg.password == PASSWORD
    assert PASSWORD not in repr(cfg)


# --- dry run --------------------------------------------------------------------------------------------

def test_dry_run_makes_no_calls_and_says_so():
    def no_network(request):
        raise AssertionError(f"dry run called {request.url}")

    c = TestClient(create_veritree_app(DRY_ENV, transport=httpx.MockTransport(no_network)))
    info = c.get("/provider").json()
    assert info["provider_id"] == "veritree-dryrun" and "test only" in info["name"]
    assert info["certification"]["standard"] == "none" and "tier" not in info
    [item] = c.get("/catalog").json()
    assert "dry run" in item["name"] and item["price_usd_per_t"] == pytest.approx(6.0)  # 1.50 / 0.25

    order = order_for(c)
    first = c.post("/fulfill", json=order).json()
    assert first["fulfillment_status"] == "fulfilled" and first["certificate_ref"].startswith("DRYRUN-")
    assert c.post("/fulfill", json=order).json() == first
    cert = c.get("/certificate/o-1").json()
    assert cert["verified"] is False and cert["kind"] == "attestation"
    assert cert["note"].startswith("DRY RUN") and "no tonnes have been removed" in cert["note"]


# --- quotes ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("co2e_g,trees", [(1_000_000, 4), (1_000_001, 5), (250_000, 1), (1, 1), (2_500_000, 10)])
def test_quote_rounds_up_to_whole_trees(live, co2e_g, trees):
    q = live.post("/quote", json={"co2e_g": co2e_g}).json()
    assert q["price_usd"] == pytest.approx(trees * 1.50)
    assert q["project_id"] == "veritree-site-7"
    assert q["fulfillment_days"] == 548  # veritree plants within 18 months


def test_catalog_reads_the_planting_site(live):
    [item] = live.get("/catalog").json()
    assert item["name"] == "Test site" and item["region"] == "5HM6QQR5+X4"
    assert item["capacity_t_co2e"] == 0  # unknown until veritree confirms inventory


# --- live orders ----------------------------------------------------------------------------------------

def test_order_lifecycle(live, fake):
    order = order_for(live)
    r = live.post("/fulfill", json=order)
    assert r.status_code == 200, r.text
    assert r.json() == {"order_id": "o-1", "fulfillment_status": "ordered", "certificate_ref": None}
    [body] = fake.created
    assert body["tree_amount"] == 4 and body["cost_per_tree"] == 1.50
    assert body["invoice_id"] == INVOICE_PREFIX + "o-1" and body["planting_site_id"] == 7
    assert body["counterparty_id"] == "42" and body["counterparty_type"] == "sponsor"
    assert body["date_requested"] < body["due_date"]

    assert live.post("/fulfill", json=order).json()["fulfillment_status"] == "ordered"
    assert len(fake.created) == 1  # idempotent

    assert live.get("/certificate/o-1").status_code == 404  # nothing planted yet
    fake.set_status(3)
    assert live.get("/status/o-1").json()["fulfillment_status"] == "fulfilling"
    assert live.get("/certificate/o-1").status_code == 404
    fake.set_status(4, allocated="4")
    s = live.get("/status/o-1").json()
    assert s["fulfillment_status"] == "fulfilled" and s["certificate_ref"] == "veritree:tree-order:pub-100"

    cert = live.get("/certificate/o-1").json()
    assert cert["verified"] is False and cert["kind"] == "attestation"
    assert "4 trees planted" in cert["note"]
    assert "not a certified carbon removal" in cert["note"] and "no tonnes have been removed" in cert["note"]


def test_cancelled_order_fails(live, fake):
    live.post("/fulfill", json=order_for(live))
    fake.set_status(5)
    assert live.get("/status/o-1").json() == {"order_id": "o-1", "fulfillment_status": "failed",
                                              "certificate_ref": None}


def test_retry_after_restart_finds_the_existing_order(fake):
    transport = httpx.MockTransport(fake.handler)
    first = TestClient(create_veritree_app(LIVE_ENV, transport=transport))
    order = order_for(first)
    first.post("/fulfill", json=order)
    restarted = TestClient(create_veritree_app(LIVE_ENV, transport=transport))
    assert restarted.get("/status/o-1").json()["fulfillment_status"] == "ordered"
    restarted.post("/fulfill", json=order)
    assert len(fake.created) == 1
    assert restarted.get("/status/unknown").status_code == 404


def test_price_change_since_quote_is_refused(live, fake):
    order = order_for(live)
    r = live.post("/fulfill", json={**order, "price_usd": order["price_usd"] - 1})
    assert r.status_code == 502 and "new quote" in r.json()["detail"]
    assert fake.created == []


def test_unknown_project_is_refused(live, fake):
    r = live.post("/fulfill", json={**order_for(live), "project_id": "elsewhere"})
    assert r.status_code == 502 and fake.created == []


# --- auth -----------------------------------------------------------------------------------------------

def test_token_is_reused_then_renewed_on_expiry_and_401(fake):
    now = [0.0]
    cfg = VeritreeConfig.from_env(LIVE_ENV)
    client = VeritreeClient(cfg, transport=httpx.MockTransport(fake.handler), clock=lambda: now[0])
    provider = VeritreeProvider(cfg, client)
    provider.get_catalog()
    provider.report_status("x")
    assert fake.logins == 1
    now[0] = 14400  # past expires_in
    provider.report_status("x")
    assert fake.logins == 2
    fake.valid_tokens.clear()  # revoked server-side
    provider.report_status("x")
    assert fake.logins == 3


def test_login_failure_never_leaks_the_password(fake):
    env = {**LIVE_ENV, "VERITREE_PASSWORD": "wrong-" + PASSWORD[::-1]}
    c = TestClient(create_veritree_app(env, transport=httpx.MockTransport(fake.handler)))
    r = c.post("/quote", json={"co2e_g": 1000})
    assert r.status_code == 200  # quoting needs no API call
    r = c.get("/catalog")
    assert r.status_code == 502 and "login failed" in r.json()["detail"]
    assert env["VERITREE_PASSWORD"] not in r.text


def test_network_errors_become_provider_errors(fake):
    def down(request):
        raise httpx.ConnectError("boom")

    cfg = VeritreeConfig.from_env(LIVE_ENV)
    provider = VeritreeProvider(cfg, VeritreeClient(cfg, transport=httpx.MockTransport(down)))
    with pytest.raises(VeritreeError, match="ConnectError"):
        provider.fulfill(FulfillRequest(order_id="o", project_id=provider.project_id, co2e_g=1, price_usd=1.5))
