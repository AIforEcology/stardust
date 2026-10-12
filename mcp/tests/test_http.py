"""Over real HTTP: the SDK's bearer check, backed by Core's key lookup (auth.py)."""

import socket
import threading
import time

import httpx2
import pytest
import uvicorn
from fastapi.testclient import TestClient
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from core_world import make_core
from stardust_mcp.auth import CoreKeyVerifier
from stardust_mcp.core import CoreClient
from stardust_mcp.reporting import build_reporting_server

pytestmark = pytest.mark.anyio

INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}}
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


@pytest.fixture
def anyio_backend():
    return "asyncio"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def mcp_url(world):
    port = free_port()
    url = f"http://127.0.0.1:{port}/mcp"
    server = build_reporting_server(world.core, public_url=url)
    uv = uvicorn.Server(uvicorn.Config(server.streamable_http_app(host="127.0.0.1"), host="127.0.0.1",
                                       port=port, log_level="warning", lifespan="on"))
    thread = threading.Thread(target=uv.run, daemon=True)
    thread.start()
    for _ in range(100):
        if uv.started:
            break
        time.sleep(0.05)
    assert uv.started
    yield url
    uv.should_exit = True
    thread.join(5)


async def post(url, headers=None):
    async with httpx2.AsyncClient() as h:
        return await h.post(url, json=INIT, headers={**MCP_HEADERS, **(headers or {})})


async def test_no_key_is_refused_with_a_bearer_challenge(world, mcp_url):
    r = await post(mcp_url)
    assert r.status_code == 401
    assert r.headers["WWW-Authenticate"].startswith("Bearer")
    assert "resource_metadata" in r.headers["WWW-Authenticate"]


@pytest.mark.parametrize("key_name,status", [("revoked", 401), ("provider_only", 403)])
async def test_bad_keys_are_refused(world, mcp_url, key_name, status):
    assert (await post(mcp_url, {"Authorization": f"Bearer {world.keys[key_name]}"})).status_code == status
    assert (await post(mcp_url, {"Authorization": "Bearer sdk_nope_nope"})).status_code == 401


async def test_a_real_client_with_a_key(world, mcp_url):
    http = httpx2.AsyncClient(headers={"Authorization": f"Bearer {world.keys['org_a']}"})
    async with Client(streamable_http_client(mcp_url, http_client=http)) as c:
        tools = {t.name for t in (await c.list_tools()).tools}
        assert "get_usage_summary" in tools
        r = await c.call_tool("get_usage_summary", {})
        assert not r.is_error and r.structured_content["requests"]["value"] == 3
        # The key on this connection is the one Core sees: org B's data stays out of reach.
        assert (await c.call_tool("get_usage_summary", {"user_id": "b1000000-0000-4000-8000-0000000000b1"})
                ).structured_content["requests"]["value"] == 0
    await http.aclose()


# --- the verifier on its own -----------------------------------------------------------------------


async def test_verifier_maps_a_key_to_its_principal(world):
    token = await CoreKeyVerifier(world.core).verify_token(world.keys["user_a1"])
    assert token.scopes == ["reporting.read"]
    assert token.claims == {"org_id": "0a000000-0000-4000-8000-00000000000a",
                            "user_id": "a1000000-0000-4000-8000-0000000000a1"}
    assert token.subject == token.claims["user_id"]
    assert await CoreKeyVerifier(world.core).verify_token(world.keys["revoked"]) is None
    assert await CoreKeyVerifier(world.core).verify_token("garbage") is None


async def test_verifier_accepts_no_one_when_core_auth_is_off(tmp_path):
    # Against an unprotected Core, every read would be unscoped: refuse every key.
    app = make_core(tmp_path, mode="off")
    with TestClient(app):
        core = CoreClient("http://core", http=httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app)))
        assert await CoreKeyVerifier(core).verify_token("sdk_anything_atall") is None


async def test_verifier_refuses_when_core_is_down():
    core = CoreClient("http://127.0.0.1:9")  # nothing listens on the discard port
    assert await CoreKeyVerifier(core).verify_token("sdk_a_b") is None
