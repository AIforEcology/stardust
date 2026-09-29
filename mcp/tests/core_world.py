"""Test world for the MCP servers: a real Stardust Core, in-process, with subscriber auth required, two organizations and a vetted
mock provider. The MCP server under test talks to it over ASGI, exactly as it would over HTTP."""

import json
from dataclasses import dataclass
from typing import Dict

import httpx
import pytest
from fastapi.testclient import TestClient

from stardust_core.api import create_app
from stardust_core.config import DEFAULT_ESC, DEFAULT_METHODOLOGY, Settings
from stardust_mcp.core import CoreClient

pytest.importorskip("stardust_provider")
from stardust_provider.examples.mock_reforestation import MockReforestationProvider  # noqa: E402
from stardust_provider.server import create_app as provider_app  # noqa: E402

ADMIN = {"X-Stardust-Admin-Token": "admin-secret"}
ORG_A = "0a000000-0000-4000-8000-00000000000a"
ORG_B = "0b000000-0000-4000-8000-00000000000b"
USER_A1 = "a1000000-0000-4000-8000-0000000000a1"
USER_A2 = "a2000000-0000-4000-8000-0000000000a2"
USER_B1 = "b1000000-0000-4000-8000-0000000000b1"

# Test-only prices, deliberately round. Not real provider pricing.
PRICES = {"claude-test-opus": {"input_cost_per_token": 1e-05, "output_cost_per_token": 5e-05}}


def event(user, org, day="2026-09-23", tokens=1000):
    return {"source_layer": "infra_agent", "provider": "anthropic", "model": "claude-test-opus",
            "region": "us-east-1", "timestamp": f"{day}T12:00:00Z", "tokens_in": tokens, "tokens_out": tokens,
            "user_id": user, "org_id": org}


@dataclass
class World:
    core_http: TestClient
    core: CoreClient
    keys: Dict[str, str]


def make_core(tmp_path, mode="required"):
    prices = tmp_path / "prices.json"
    prices.write_text(json.dumps(PRICES))
    settings = Settings(DEFAULT_METHODOLOGY, DEFAULT_ESC, prices, "https://example.org/donate", "admin-secret",
                        subscriber_auth=mode)
    provider_http = httpx.AsyncClient(transport=httpx.ASGITransport(app=provider_app(MockReforestationProvider())))
    return create_app(settings, http=provider_http)
