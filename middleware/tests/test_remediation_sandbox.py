"""Keys on remediation (STARDUST_REMEDIATION_AUTH) and sandbox mode: sandbox and live never mix."""

import uuid
from typing import Dict

import httpx
import pytest
from fastapi.testclient import TestClient

from stardust_core.api import create_app
from stardust_core.broker import SANDBOX_NOTICE
from stardust_core.config import DEFAULT_ESC, DEFAULT_METHODOLOGY, Settings

provider_pkg = pytest.importorskip("stardust_provider")
from stardust_provider.examples.mock_reforestation import MockReforestationProvider  # noqa: E402
from stardust_provider.server import create_app as provider_app  # noqa: E402

ADMIN = {"X-Stardust-Admin-Token": "s3cret"}
ORG_A, ORG_B = str(uuid.uuid4()), str(uuid.uuid4())
ALL = ["ingest.write", "reporting.read", "remediation.write"]
EVENT = {"source_layer": "infra_agent", "provider": "anthropic", "model": "claude-test-opus", "region": "us-east-1",
         "timestamp": "2026-11-19T12:00:00Z", "tokens_in": 500, "tokens_out": 500}


class Router(httpx.AsyncBaseTransport):
    """Routes broker calls to one of several in-process provider apps by host name."""

    def __init__(self, apps: Dict[str, object]):
        self.transports = {host: httpx.ASGITransport(app=app) for host, app in apps.items()}

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return await self.transports[request.url.host].handle_async_request(request)


@pytest.fixture
def core(pricing_file):
    http = httpx.AsyncClient(transport=Router({
        "live": provider_app(MockReforestationProvider("live-forest", price_usd_per_t=30.0)),
        "sandbox": provider_app(MockReforestationProvider()),
    }))
    settings = Settings(DEFAULT_METHODOLOGY, DEFAULT_ESC, pricing_file, "https://example.org/donate", "s3cret",
                        subscriber_auth="required", ingest_auth="required", remediation_auth="required")
    with TestClient(create_app(settings, http=http)) as c:
        for url in ("http://live", "http://sandbox"):
            assert c.post("/v1/admin/providers", json={"base_url": url}, headers=ADMIN).status_code == 200
        assert c.post("/v1/admin/providers/live-forest/approve", json={"tier": "emerging"},
                      headers=ADMIN).status_code == 200
        r = c.post("/v1/admin/providers/mock-reforestation/approve", json={"tier": "emerging", "sandbox": True},
                   headers=ADMIN)
        assert r.status_code == 200 and r.json()["sandbox"] is True
        yield c


def key(c, org=ORG_A, sandbox=False, scopes=ALL, user=None) -> Dict[str, str]:
    body = {"org_id": org, "scopes": scopes, "sandbox": sandbox}
    if user:
        body["user_id"] = user
    r = c.post("/v1/admin/keys", json=body, headers=ADMIN)
    assert r.status_code == 200, r.text
    assert r.json()["sandbox"] is sandbox
    return {"Authorization": f"Bearer {r.json()['key']}"}


def quote(c, headers, co2e_g=1_000_000):
    r = c.post("/v1/remediation/quotes", json={"co2e_g": co2e_g}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["quotes"]


def test_remediation_without_a_key_is_refused(core):
    assert core.post("/v1/remediation/quotes", json={"co2e_g": 1000}).status_code == 401
    assert core.post("/v1/remediation/orders", json={"quote_id": str(uuid.uuid4()),
                                                     "subscriber_id": "x"}).status_code == 401
    r = core.get(f"/v1/remediation/orders/{uuid.uuid4()}")
    assert r.status_code == 401 and r.json()["reason"] == "unauthorized"
    assert core.post("/v1/remediation/quotes", json={"co2e_g": 1000},
                     headers={"Authorization": "Bearer sdk_nope_nope"}).status_code == 401


def test_remediation_needs_the_scope(core):
    reader = key(core, scopes=["reporting.read"])
    r = core.post("/v1/remediation/quotes", json={"co2e_g": 1000}, headers=reader)
    assert r.status_code == 403 and r.json()["reason"] == "forbidden_scope"


def test_sandbox_and_live_keys_never_see_each_others_providers(core):
    live, sandbox = key(core), key(core, sandbox=True)
    [lq] = quote(core, live)
    assert lq["provider_id"] == "live-forest" and lq["sandbox"] is False
    [sq] = quote(core, sandbox)
    assert sq["provider_id"] == "mock-reforestation" and sq["sandbox"] is True
    # Tier preference never widens across the line.
    assert core.post("/v1/remediation/quotes", json={"co2e_g": 1000, "tier_preference": ["emerging"]},
                     headers=sandbox).json()["quotes"][0]["provider_id"] == "mock-reforestation"
    # Sandbox providers aren't in the public list.
    assert [p["provider_id"] for p in core.get("/v1/providers").json()["providers"]] == ["live-forest"]


def test_sandbox_order_is_labelled_everywhere(core):
    sandbox = key(core, sandbox=True)
    [q] = quote(core, sandbox)
    r = core.post("/v1/remediation/orders", json={"quote_id": q["quote_id"]}, headers=sandbox)
    assert r.status_code == 200, r.text
    order = r.json()
    assert order["sandbox"] is True and order["notice"] == SANDBOX_NOTICE
    assert order["org_id"] == ORG_A and order["subscriber_id"] == ORG_A
    assert order["certificate"]["sandbox"] is True and "not a real removal" in order["certificate"]["note"]
    assert order["certificate_ref"].startswith("SANDBOX-")

    status = core.get(f"/v1/remediation/orders/{order['remediation_order_id']}", headers=sandbox).json()
    assert status["fulfillment_status"] == "fulfilled"
    assert status["sandbox"] is True and status["notice"] == SANDBOX_NOTICE and status["certificate"]["sandbox"]

    live = key(core)
    [lq] = quote(core, live)
    live_order = core.post("/v1/remediation/orders", json={"quote_id": lq["quote_id"]}, headers=live).json()
    assert live_order["sandbox"] is False and "notice" not in live_order
    assert live_order["certificate"]["sandbox"] is False


def test_quotes_and_orders_dont_cross_sandbox_or_organizations(core):
    live_a, sandbox_a, live_b = key(core), key(core, sandbox=True), key(core, org=ORG_B)
    [lq] = quote(core, live_a)
    [sq] = quote(core, sandbox_a)
    # The wrong key can't use a quote, and trying doesn't burn it for its owner.
    assert core.post("/v1/remediation/orders", json={"quote_id": lq["quote_id"]}, headers=sandbox_a).status_code == 400
    assert core.post("/v1/remediation/orders", json={"quote_id": sq["quote_id"]}, headers=live_a).status_code == 400
    assert core.post("/v1/remediation/orders", json={"quote_id": lq["quote_id"]}, headers=live_b).status_code == 400
    order = core.post("/v1/remediation/orders", json={"quote_id": lq["quote_id"]}, headers=live_a).json()
    oid = order["remediation_order_id"]

    # Only the owning organization, on the same side of the sandbox, can see an order.
    assert core.get(f"/v1/remediation/orders/{oid}", headers=live_a).status_code == 200
    assert core.get(f"/v1/remediation/orders/{oid}", headers=live_b).status_code == 404
    assert core.get(f"/v1/remediation/orders/{oid}", headers=sandbox_a).status_code == 404


def test_user_level_key_sees_only_its_own_orders(core):
    alice, bob = str(uuid.uuid4()), str(uuid.uuid4())
    k_alice, k_bob = key(core, user=alice), key(core, user=bob)
    org_key = key(core)
    [q] = quote(core, k_alice)
    order = core.post("/v1/remediation/orders", json={"quote_id": q["quote_id"]}, headers=k_alice).json()
    assert order["user_id"] == alice and order["subscriber_id"] == alice
    oid = order["remediation_order_id"]
    assert core.get(f"/v1/remediation/orders/{oid}", headers=k_bob).status_code == 404
    assert core.get(f"/v1/remediation/orders/{oid}", headers=org_key).status_code == 200


def test_sandbox_orders_are_left_out_of_fees(core):
    sandbox, live = key(core, sandbox=True), key(core)
    for _ in range(2):
        [q] = quote(core, sandbox)
        assert core.post("/v1/remediation/orders", json={"quote_id": q["quote_id"]}, headers=sandbox).status_code == 200
    fees = core.get("/v1/admin/fees", headers=ADMIN).json()
    assert fees["orders"] == 0 and fees["fees_usd"] == 0 and fees["by_status"] == {}

    [q] = quote(core, live)
    core.post("/v1/remediation/orders", json={"quote_id": q["quote_id"]}, headers=live)
    fees = core.get("/v1/admin/fees", headers=ADMIN).json()
    assert fees["orders"] == 1
    assert fees["provider_payouts_usd"] == pytest.approx(30.0)  # the live provider's price only


def test_sandbox_events_stay_out_of_live_totals(core):
    sandbox, live = key(core, sandbox=True), key(core)
    r = core.post("/v1/events", json=EVENT, headers=sandbox)
    assert r.status_code == 200 and r.json()["sandbox"] is True and r.json()["org_id"] == ORG_A
    assert "sandbox" not in core.post("/v1/events", json=EVENT, headers=live).json()

    live_sum = core.get("/v1/summary", headers=live).json()
    assert live_sum["events"] == 1 and "sandbox" not in live_sum
    sandbox_sum = core.get("/v1/summary", headers=sandbox).json()
    assert sandbox_sum["events"] == 1 and sandbox_sum["sandbox"] is True

    assert [d["events"] for d in core.get("/v1/summary/daily", headers=live).json()["days"]] == [1]
    daily = core.get("/v1/summary/daily", headers=sandbox).json()
    assert daily["sandbox"] is True and [d["events"] for d in daily["days"]] == [1]
    assert len(core.get("/v1/events", headers=live).json()) == 1
    assert len(core.get("/v1/events", headers=sandbox).json()) == 1
    assert core.get("/v1/auth/principal", headers=sandbox).json()["sandbox"] is True


def test_health_shows_remediation_auth_and_provider_counts(core):
    body = core.get("/healthz").json()
    assert body["remediation_auth"] == "required"
    assert body["providers"] == {"live": 1, "sandbox": 1}


def test_a_sandbox_provider_cant_be_verified(core):
    r = core.post("/v1/admin/providers/mock-reforestation/approve", json={"tier": "verified", "sandbox": True},
                  headers=ADMIN)
    assert r.status_code == 400


def test_without_remediation_auth_calls_work_as_before(pricing_file):
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=provider_app(MockReforestationProvider())))
    settings = Settings(DEFAULT_METHODOLOGY, DEFAULT_ESC, pricing_file, "https://example.org/donate", "s3cret")
    with TestClient(create_app(settings, http=http)) as c:
        c.post("/v1/admin/providers", json={"base_url": "http://mock"}, headers=ADMIN)
        c.post("/v1/admin/providers/mock-reforestation/approve", json={"tier": "emerging"}, headers=ADMIN)
        assert c.get("/healthz").json()["remediation_auth"] == "off"
        [q] = c.post("/v1/remediation/quotes", json={"co2e_g": 1000}).json()["quotes"]
        assert c.post("/v1/remediation/orders", json={"quote_id": q["quote_id"]}).status_code == 422  # needs subscriber_id
        assert c.post("/v1/remediation/orders", json={"quote_id": q["quote_id"], "subscriber_id": "app"}).status_code == 200
