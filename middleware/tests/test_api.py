from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from stardust_core.api import create_app
from stardust_core.config import DEFAULT_ESC, DEFAULT_METHODOLOGY, Settings
from stardust_core.store import SCHEMA_VERSION

EVENT = {
    "source_layer": "infra_agent",
    "provider": "anthropic",
    "model": "claude-test-opus",
    "region": "us-east-1",
    "timestamp": "2026-09-23T12:00:00-07:00",
    "tokens_in": 500,
    "tokens_out": 500,
    "user_id": "7b0c1f5e-8a9f-4a39-9d1c-2f6b1c0e9a11",
}


def settings(pricing: Path, admin_token=None) -> Settings:
    return Settings(DEFAULT_METHODOLOGY, DEFAULT_ESC, pricing, "https://example.org/donate", admin_token)


@pytest.fixture
def client(pricing_file):
    with TestClient(create_app(settings(pricing_file))) as c:
        yield c


def test_health(client):
    body = client.get("/healthz").json()
    assert {k: body[k] for k in ("ok", "methodology_version", "priced_models", "otlp_export", "otlp_grpc_receiver")} == {
        "ok": True, "methodology_version": "0.2.0", "priced_models": 3, "otlp_export": None, "otlp_grpc_receiver": None,
    }
    assert body["database"]["schema_version"] == SCHEMA_VERSION


def test_ingest_and_summary(client):
    r = client.post("/v1/events", json=EVENT)
    assert r.status_code == 200, r.text
    e = r.json()
    assert e["indicator_code"] == "C3-S"
    assert e["job_sji"].split("-")[1] == "NGP"
    client.post("/v1/events", json=EVENT)

    s = client.get("/v1/summary", params={"user_id": EVENT["user_id"]}).json()
    assert s["events"] == 2
    assert s["tokens_in"] == 1000
    assert s["cost_usd"] == pytest.approx(0.06)
    assert s["indicator_code"] == "C3-S"
    assert client.get("/v1/summary", params={"user_id": "00000000-0000-4000-8000-000000000000"}).json()["events"] == 0
    assert len(client.get("/v1/events", params={"limit": 1}).json()) == 1


def test_rejects_content_and_unknown_fields(client):
    assert client.post("/v1/events", json={**EVENT, "prompt": "hi"}).status_code == 422
    assert client.post("/v1/events", json={**EVENT, "tokens_in": -1}).status_code == 422


def test_cached_tokens(client):
    r = client.post("/v1/events", json={**EVENT, "tokens_cached_in": 400})
    assert r.status_code == 200
    assert r.json()["cost_usd"] == pytest.approx(100 * 1e-05 + 400 * 1e-06 + 500 * 5e-05)
    assert client.post("/v1/events", json={**EVENT, "tokens_cached_in": 501}).status_code == 422


def test_methodology_endpoint(client):
    assert client.get("/v1/methodology").json()["version"] == "0.2.0"


def test_donation_links_out_only(client):
    body = client.post("/v1/donations", json={"amount_usd": 5}).json()
    assert body["donation_url"] == "https://example.org/donate"
    assert "not an offset" in body["note"]


def test_admin_disabled_without_token(client):
    r = client.post("/v1/admin/providers", json={"base_url": "http://x"})
    assert r.status_code == 403


# --- broker end-to-end against the mock provider --------------------------------

provider_pkg = pytest.importorskip("stardust_provider")
from stardust_provider.examples.mock_reforestation import MockReforestationProvider  # noqa: E402
from stardust_provider.server import create_app as provider_app  # noqa: E402


@pytest.fixture
def broker_client(pricing_file):
    transport = httpx.ASGITransport(app=provider_app(MockReforestationProvider()))
    http = httpx.AsyncClient(transport=transport)
    with TestClient(create_app(settings(pricing_file, admin_token="s3cret"), http=http)) as c:
        yield c


ADMIN = {"X-Stardust-Admin-Token": "s3cret"}


def test_broker_flow(broker_client):
    c = broker_client
    assert c.post("/v1/admin/providers", json={"base_url": "http://mock"}, headers={"X-Stardust-Admin-Token": "no"}).status_code == 401

    reg = c.post("/v1/admin/providers", json={"base_url": "http://mock"}, headers=ADMIN)
    assert reg.status_code == 200, reg.text
    assert reg.json()["status"] == "pending"

    # Pending providers are invisible to subscribers.
    assert c.get("/v1/providers").json()["providers"] == []
    assert c.post("/v1/remediation/quotes", json={"co2e_g": 1000}).json()["quotes"] == []

    # Uncertified providers can't be approved as verified (§10.6).
    assert c.post("/v1/admin/providers/mock-reforestation/approve", json={"tier": "verified"}, headers=ADMIN).status_code == 400
    assert c.post("/v1/admin/providers/mock-reforestation/approve", json={"tier": "emerging"}, headers=ADMIN).status_code == 200

    quotes = c.post("/v1/remediation/quotes", json={"co2e_g": 1_000_000}).json()["quotes"]
    assert len(quotes) == 1
    assert quotes[0]["price_usd"] == pytest.approx(20.0)  # 1 t at $20/t
    assert quotes[0]["provider_tier"] == "emerging"
    assert c.post("/v1/remediation/quotes", json={"co2e_g": 1000, "tier_preference": ["verified"]}).json()["quotes"] == []

    order = c.post("/v1/remediation/orders", json={"quote_id": quotes[0]["quote_id"], "subscriber_id": "test-app"}).json()
    assert order["fulfillment_status"] == "fulfilled"
    assert order["certificate_ref"].startswith("MOCK-ATTESTATION-")
    assert order["provider_tier"] == "emerging"

    # A quote can only be used once.
    again = c.post("/v1/remediation/orders", json={"quote_id": quotes[0]["quote_id"], "subscriber_id": "test-app"})
    assert again.status_code == 400

    status = c.get(f"/v1/remediation/orders/{order['remediation_order_id']}").json()
    assert status["fulfillment_status"] == "fulfilled"

    # Delisting removes the provider from new quotes.
    c.post("/v1/admin/providers/mock-reforestation/delist", headers=ADMIN)
    assert c.post("/v1/remediation/quotes", json={"co2e_g": 1000}).json()["quotes"] == []
