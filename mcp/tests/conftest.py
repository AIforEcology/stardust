"""The ``world`` fixture. Helpers live in core_world.py: a module named ``conftest`` can clash with
middleware/tests/conftest.py when both suites run in one pytest session (as CI does)."""

import httpx2
import pytest
from fastapi.testclient import TestClient

from core_world import ADMIN, ORG_A, ORG_B, USER_A1, USER_A2, USER_B1, World, event, make_core
from stardust_mcp.core import CoreClient


@pytest.fixture
def world(tmp_path):
    app = make_core(tmp_path)
    with TestClient(app) as c:
        for user, org, day in ((USER_A1, ORG_A, "2026-09-22"), (USER_A1, ORG_A, "2026-09-23"),
                               (USER_A2, ORG_A, "2026-09-23"), (USER_B1, ORG_B, "2026-09-23")):
            assert c.post("/v1/events", json=event(user, org, day)).status_code == 200
        c.post("/v1/admin/providers", json={"base_url": "http://mock"}, headers=ADMIN)
        c.post("/v1/admin/providers/mock-reforestation/approve", json={"tier": "emerging"}, headers=ADMIN)

        def issue(**body):
            r = c.post("/v1/admin/keys", json=body, headers=ADMIN)
            assert r.status_code == 200, r.text
            return r.json()

        keys = {
            "org_a": issue(org_id=ORG_A)["key"],
            "user_a1": issue(org_id=ORG_A, user_id=USER_A1)["key"],
            "org_b": issue(org_id=ORG_B)["key"],
            "provider_only": issue(org_id=ORG_A, scopes=["provider.read"])["key"],
        }
        revoked = issue(org_id=ORG_A)
        c.post(f"/v1/admin/keys/{revoked['key_id']}/revoke", headers=ADMIN)
        keys["revoked"] = revoked["key"]

        core = CoreClient("http://core", http=httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app)))
        yield World(core_http=c, core=core, keys=keys)
