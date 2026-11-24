"""The veritree driver behind Core's broker, against a fake veritree API (no live calls)."""

import uuid
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from stardust_core.api import create_app
from stardust_core.config import DEFAULT_ESC, DEFAULT_METHODOLOGY, Settings

pytest.importorskip("stardust_provider.providers.veritree")
from stardust_provider.providers.veritree import create_veritree_app  # noqa: E402

FAKE = Path(__file__).resolve().parents[2] / "providers-service" / "tests"
ADMIN = {"X-Stardust-Admin-Token": "s3cret"}
ORG = str(uuid.uuid4())


@pytest.fixture
def fake_veritree(monkeypatch):
    monkeypatch.syspath_prepend(str(FAKE))
    from test_veritree import LIVE_ENV, FakeVeritree  # the providers-service test fake

    fake = FakeVeritree()
    return fake, create_veritree_app(LIVE_ENV, transport=httpx.MockTransport(fake.handler))


@pytest.fixture
def core(pricing_file, fake_veritree):
    fake, provider = fake_veritree
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=provider))
    settings = Settings(DEFAULT_METHODOLOGY, DEFAULT_ESC, pricing_file, "https://example.org/donate", "s3cret",
                        subscriber_auth="required", ingest_auth="required", remediation_auth="required")
    with TestClient(create_app(settings, http=http)) as c:
        r = c.post("/v1/admin/providers", json={"base_url": "http://veritree"}, headers=ADMIN)
        assert r.status_code == 200 and r.json()["provider_id"] == "veritree"
        yield c, fake


def test_veritree_cannot_be_verified_tier(core):
    c, _ = core
    r = c.post("/v1/admin/providers/veritree/approve", json={"tier": "verified"}, headers=ADMIN)
    assert r.status_code == 400 and "Verra or Gold Standard" in r.text


def test_order_through_core(core):
    c, fake = core
    assert c.post("/v1/admin/providers/veritree/approve", json={"tier": "emerging"}, headers=ADMIN).status_code == 200
    r = c.post("/v1/admin/keys", json={"org_id": ORG, "scopes": ["remediation.write", "reporting.read"]},
               headers=ADMIN)
    headers = {"Authorization": f"Bearer {r.json()['key']}"}

    [q] = c.post("/v1/remediation/quotes", json={"co2e_g": 1_000_000}, headers=headers).json()["quotes"]
    assert q["provider_id"] == "veritree" and q["provider_tier"] == "emerging"
    assert q["price_usd"] == pytest.approx(6.0)  # 4 trees at the test price
    order = c.post("/v1/remediation/orders", json={"quote_id": q["quote_id"]}, headers=headers).json()
    assert order["fulfillment_status"] == "ordered" and order["certificate"] is None
    assert [o["tree_amount"] for o in fake.created] == [4]

    oid = order["remediation_order_id"]
    fake.set_status(4, allocated="4")
    status = c.get(f"/v1/remediation/orders/{oid}", headers=headers).json()
    assert status["fulfillment_status"] == "fulfilled"
    assert status["certificate_ref"] == "veritree:tree-order:pub-100"
    assert fake.created[0]["invoice_id"] == f"stardust-{oid}"
