import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from stardust_provider.examples.mock_reforestation import MockReforestationProvider
from stardust_provider.server import create_app
from stardust_provider.telemetry import TelemetryRecord

SCHEMA = Path(__file__).resolve().parents[2] / "schema" / "provider-telemetry.schema.json"


@pytest.fixture
def client():
    return TestClient(create_app(MockReforestationProvider()))


def test_contract(client):
    info = client.get("/provider").json()
    assert info["provider_id"] == "mock-reforestation"
    assert "tier" not in info  # tiers are assigned by the broker, never self-declared
    assert client.get("/catalog").json()[0]["price_usd_per_t"] == 20.0

    q = client.post("/quote", json={"co2e_g": 500_000}).json()
    assert q["price_usd"] == pytest.approx(10.0)
    assert client.post("/quote", json={"co2e_g": 0}).status_code == 422

    order = {"order_id": "o-1", "project_id": q["project_id"], "co2e_g": 500_000, "price_usd": 10.0}
    first = client.post("/fulfill", json=order).json()
    assert first["fulfillment_status"] == "fulfilled"
    assert client.post("/fulfill", json=order).json() == first  # idempotent

    assert client.get("/status/o-1").json()["fulfillment_status"] == "fulfilled"
    assert client.get("/certificate/o-1").json()["verified"] is False
    assert client.get("/status/nope").status_code == 404
    assert client.get("/certificate/nope").status_code == 404


RECORD = {
    "FPI": "dac-demo-001",
    "tech_type": "DAC",
    "timestamp": "2026-09-23T00:00:00Z",
    "DST": "direct_metered",
    "CRI": 41.7,
    "CEP": 88.5,
    "CCM": 12000,
    "SUP": "geologic",
    "ECT": 1650,
    "ESM": {"grid": 20, "renewable": 80},
    "GLC": {"lat": 64.04, "lon": -21.4},
    "RFI": "hourly",
}


def test_telemetry_model_and_endpoint(client):
    assert TelemetryRecord(**RECORD).tech_type.value == "DAC"
    assert client.post("/telemetry", json=RECORD).status_code == 200
    assert client.post("/telemetry", json={**RECORD, "CEP": 140}).status_code == 422
    assert client.post("/telemetry", json={**RECORD, "D-CEP": 90}).status_code == 422  # derived fields not accepted


def test_telemetry_matches_json_schema():
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(SCHEMA.read_text())
    jsonschema.Draft202012Validator(schema).validate(RECORD)
    # The pydantic model and JSON Schema must accept the same field codes.
    assert set(TelemetryRecord.model_fields) == set(schema["properties"])
