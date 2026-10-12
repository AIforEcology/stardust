"""The Subscriber reporting server (§23.3), against a real in-process Core."""

import json

import pytest
from mcp import Client

from core_world import USER_A2, USER_B1
from stardust_mcp.reporting import build_reporting_server
from stardust_mcp.spec import SCHEMA_DIR, spec_version

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def server_for(world, key_name):
    key = world.keys.get(key_name) if key_name else None
    return build_reporting_server(world.core, public_url="http://mcp.test/mcp", key_source=lambda: key)


async def call(world, key_name, tool, **args):
    async with Client(server_for(world, key_name)) as c:
        return await c.call_tool(tool, args)


def error(result):
    assert result.is_error, result.structured_content
    return result.content[0].text


def core_summary(world, key_name, **params):
    return world.core_http.get("/v1/summary", params=params,
                               headers={"Authorization": f"Bearer {world.keys[key_name]}"}).json()


# --- tool list -------------------------------------------------------------------------------------


async def test_tools_are_the_spec_subset_and_all_read_only(world):
    async with Client(server_for(world, "org_a")) as c:
        tools = (await c.list_tools()).tools
    spec = {t["tool"] for t in json.loads((SCHEMA_DIR / "mcp-tools.json").read_text())["tools"]
            if t["section"] == "23.3"}
    assert {t.name for t in tools} == {"get_usage_summary", "get_impact_summary", "explain_metric", "quote_credits"}
    assert {t.name for t in tools} <= spec
    for t in tools:
        assert t.annotations.read_only_hint is True and t.annotations.destructive_hint is False
        assert t.output_schema  # structured output (§23.7)


# --- usage and impact ------------------------------------------------------------------------------


async def test_usage_summary_is_limited_to_the_keys_org(world):
    r = await call(world, "org_a", "get_usage_summary")
    s = r.structured_content
    assert not r.is_error
    assert s["requests"] == {"value": 3.0, "unit": "{request}", "field_code": None}
    assert s["tokens_in"]["value"] == 3000 and s["tokens_out"]["value"] == 3000
    assert s["cost_usd"] == {"value": pytest.approx(core_summary(world, "org_a")["cost_usd"]), "unit": "USD", "field_code": None}
    assert s["versions"]["spec_version"] == spec_version()
    assert s["summary"].startswith("3 requests, 6,000 tokens")

    # The other org sees only its own.
    assert (await call(world, "org_b", "get_usage_summary")).structured_content["requests"]["value"] == 1


async def test_usage_by_user_period_and_day(world):
    by_user = (await call(world, "org_a", "get_usage_summary", user_id=USER_A2)).structured_content
    assert by_user["requests"]["value"] == 1

    day = (await call(world, "org_a", "get_usage_summary", since="2026-09-23", until="2026-09-24")).structured_content
    assert day["requests"]["value"] == 2
    assert day["period"] == {"since": "2026-09-23T00:00:00+00:00", "until": "2026-09-24T00:00:00+00:00"}

    rows = (await call(world, "org_a", "get_usage_summary", group_by="day")).structured_content["by_day"]
    assert [(r["day"], r["requests"]) for r in rows] == [("2026-09-22", 1), ("2026-09-23", 2)]


async def test_user_key_is_pinned_to_its_user(world):
    assert (await call(world, "user_a1", "get_usage_summary")).structured_content["requests"]["value"] == 2
    assert "[forbidden_scope]" in error(await call(world, "user_a1", "get_usage_summary", user_id=USER_A2))


async def test_another_orgs_user_reads_as_empty_never_their_data(world):
    s = (await call(world, "org_a", "get_usage_summary", user_id=USER_B1)).structured_content
    assert s["requests"]["value"] == 0 and s["indicator_code"] is None


async def test_impact_summary_splits_carbon_and_discloses_gaps(world):
    s = (await call(world, "org_a", "get_impact_summary")).structured_content
    core = core_summary(world, "org_a")
    assert s["carbon"] == {"OPE": {"value": pytest.approx(core["co2e_g"]), "unit": "gCO2e", "field_code": "OPE"}}
    assert s["carbon_not_yet_measured"] == ["FAC", "TRA", "EMB"]
    assert s["energy"]["value"] == pytest.approx(core["energy_wh"]) and s["energy"]["unit"] == "Wh"
    assert s["water_onsite"]["value"] + s["water_offsite"]["value"] == pytest.approx(s["water_total"]["value"])
    assert s["heat_recovered"]["value"] is None  # no ERF reported: unknown, not zero
    assert s["versions"]["methodology_version"] == core["methodology_version"]


# --- explain_metric --------------------------------------------------------------------------------


async def test_explain_metric(world):
    s = (await call(world, "org_a", "explain_metric", code="ope")).structured_content
    assert (s["code"], s["name"], s["unit"], s["section"], s["derived"]) == (
        "OPE", "Operational Inference Emissions", "gCO2e", "8.8.1", False)
    assert s["details"]["frequency"] == "Per call"

    d = (await call(world, "org_a", "explain_metric", code="D-RSS")).structured_content
    assert d["derived"] is True and d["section"] == "21.3"
    assert (await call(world, "org_a", "explain_metric", code="HYD")).structured_content["name"] == "Hydroelectric power plant"
    assert "[not_found]" in error(await call(world, "org_a", "explain_metric", code="XYZ"))


# --- quote_credits ---------------------------------------------------------------------------------


async def test_quote_for_a_period_uses_its_footprint(world):
    s = (await call(world, "org_a", "quote_credits", since="2026-09-23", until="2026-09-24")).structured_content
    expected = core_summary(world, "org_a", since="2026-09-23T00:00:00Z", until="2026-09-24T00:00:00Z")["co2e_g"]
    assert s["co2e"]["value"] == pytest.approx(expected)
    assert s["dimension"] == "carbon" and len(s["quotes"]) == 1
    assert s["quotes"][0]["provider_id"] == "mock-reforestation"
    assert "never purchases" in s["how_to_buy"]


async def test_quote_for_an_amount(world):
    s = (await call(world, "org_a", "quote_credits", co2e_g=5000)).structured_content
    assert s["co2e"]["value"] == 5000 and s["quotes"][0]["co2e_g"] == 5000


async def test_nothing_to_quote(world):
    text = error(await call(world, "org_a", "quote_credits", since="2020-01-01", until="2020-02-01"))
    assert "[invalid_period]" in text


# --- errors ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("args", [{"since": "last week"}, {"since": "2026-09-24", "until": "2026-09-23"}])
async def test_bad_periods(world, args):
    assert "[invalid_period]" in error(await call(world, "org_a", "get_usage_summary", **args))


@pytest.mark.parametrize("key_name,reason", [
    ("provider_only", "[forbidden_scope]"),  # no reporting.read
    ("revoked", "[unauthorized]"),
    (None, "[unauthorized]"),
])
async def test_access_is_refused(world, key_name, reason):
    for tool, args in (("get_usage_summary", {}), ("get_impact_summary", {}), ("quote_credits", {"since": "2026-09-01"})):
        assert reason in error(await call(world, key_name, tool, **args))
