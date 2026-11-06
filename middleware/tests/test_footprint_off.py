"""With STARDUST_FOOTPRINT_FILE unset, Core must answer exactly as it did before the footprint overlay.

`data/footprint_off_golden.json` was captured from Core on main before the overlay was added
(commit 387908b), with only the random part of each job SJI masked (its energy source code segment
is kept). Responses are compared field by field in order, with exact types and values. Not the raw
text: how a float is spelled (5.6e-05 or 0.000056) depends on the installed pydantic-core version,
not on Core, and CI runs more than one.
"""

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from stardust_core.api import create_app
from stardust_core.config import DEFAULT_ESC, DEFAULT_METHODOLOGY, Settings, load_json

GOLDEN = Path(__file__).parent / "data" / "footprint_off_golden.json"

_TS = "2026-10-21T12:00:00Z"
EVENTS = [
    # No region: the provider's default grid. These are the events a footprint would change.
    {"event_id": "00000000-0000-4000-8000-000000000001", "source_layer": "browser_ext", "provider": "anthropic",
     "model": "claude-opus-5-5", "timestamp": _TS, "tokens_in": 2000, "tokens_out": 500},
    {"event_id": "00000000-0000-4000-8000-000000000002", "source_layer": "browser_ext", "provider": "openai",
     "model": "gpt-5", "timestamp": _TS, "tokens_in": 1200, "tokens_out": 300, "tokens_estimated": True},
    {"event_id": "00000000-0000-4000-8000-000000000003", "source_layer": "chat_plugin", "provider": "google",
     "model": "gemini-2.5-flash", "timestamp": _TS, "tokens_in": 100, "tokens_out": 50},
    {"event_id": "00000000-0000-4000-8000-000000000004", "source_layer": "browser_ext", "provider": "mistral",
     "model": "unknown", "timestamp": _TS},
    # A named region wins over any provider default.
    {"event_id": "00000000-0000-4000-8000-000000000005", "source_layer": "infra_agent", "provider": "anthropic",
     "model": "claude-test-opus", "timestamp": _TS, "region": "us-east-1", "tokens_in": 500, "tokens_out": 500,
     "tokens_cached_in": 100},
    {"event_id": "00000000-0000-4000-8000-000000000006", "source_layer": "infra_agent", "provider": "openai",
     "model": "gpt-4o-mini", "timestamp": _TS, "region": "swedencentral", "tokens_in": 10, "tokens_out": 10,
     "energy_reuse_factor": 0.2},
]

_SJI = re.compile(r'"job_sji":"[0-9A-F]{8}-([A-Z]{3})-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}"')


def mask(body: str) -> str:
    return _SJI.sub(lambda m: f'"job_sji":"XXXXXXXX-{m.group(1)}-XXXX-XXXX-XXXXXXXXXXXX"', body)


def exact(body: str) -> list:
    """Key order, types and exact values of a JSON body (an int 1 never equals a float 1.0 here)."""
    def typed(v):
        if isinstance(v, list):
            return ["list", [typed(x) for x in v]]
        if isinstance(v, tuple):  # an object, from object_pairs_hook
            return v
        return [type(v).__name__, v]
    return json.loads(body, object_pairs_hook=lambda pairs: tuple((k, typed(v)) for k, v in pairs))


@pytest.fixture
def client(pricing_file):
    with TestClient(create_app(Settings(DEFAULT_METHODOLOGY, DEFAULT_ESC, pricing_file, "https://example.org/donate",
                                        None))) as c:
        yield c


def test_footprint_is_off_by_default(monkeypatch):
    monkeypatch.delenv("STARDUST_FOOTPRINT_FILE", raising=False)
    assert Settings.from_env().footprint_path is None


def test_events_are_unchanged(client):
    golden = json.loads(GOLDEN.read_text())
    got = [mask(client.post("/v1/events", json=e).text) for e in EVENTS]
    assert [exact(b) for b in got] == [exact(b) for b in golden["events"]]


def test_health_and_methodology_unchanged(client):
    golden = json.loads(GOLDEN.read_text())
    body = client.get("/healthz").json()
    assert sorted(body) == golden["healthz_keys"]
    assert body["methodology_version"] == "0.2.0"
    assert client.get("/v1/methodology").json() == load_json(DEFAULT_METHODOLOGY)
