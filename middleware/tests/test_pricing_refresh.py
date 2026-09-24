import asyncio
import json
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from stardust_core.api import create_app
from stardust_core.config import DEFAULT_ESC, DEFAULT_METHODOLOGY, Settings, parse_refresh
from stardust_core.pricing import PricingTable
from stardust_core.pricing_refresh import MIN_PRICED_MODELS, PricingRefresher, load_initial

URL = "https://pricing.test/model_prices.json"


def raw_prices(n: int, price: float = 1e-06, extra=None) -> dict:
    raw = {f"model-{i}": {"input_cost_per_token": price, "output_cost_per_token": price * 4} for i in range(n)}
    raw.update(extra or {})
    return raw


def refresher(responses, table: PricingTable, cache: Path = None):
    """A refresher whose HTTP client serves ``responses`` in order (the last one repeats)."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url)
        return responses[min(len(calls), len(responses)) - 1]

    state = {"table": table}
    r = PricingRefresher(
        get_table=lambda: state["table"],
        set_table=lambda t: state.update(table=t),
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        url=URL,
        cache_path=cache,
    )
    return r, state, calls


def test_parse_refresh():
    assert parse_refresh("off") is None
    assert parse_refresh("0") is None
    assert parse_refresh("once") == 0
    assert parse_refresh("24") == 24
    assert parse_refresh("0.5") == 0.5
    with pytest.raises(ValueError):
        parse_refresh("-1")
    with pytest.raises(ValueError):
        parse_refresh("daily")


def test_refresh_swaps_diffs_and_caches(tmp_path):
    old = PricingTable.from_raw(raw_prices(150), source="old")
    new_raw = raw_prices(150, extra={"model-new": {"input_cost_per_token": 2e-06, "output_cost_per_token": 8e-06}})
    new_raw["model-0"]["input_cost_per_token"] = 9e-06  # re-priced
    del new_raw["model-1"]  # removed
    cache = tmp_path / "cache" / "prices.json"

    r, state, _ = refresher([httpx.Response(200, json=new_raw)], old, cache)
    status = asyncio.run(r.refresh_once())

    assert status["last_error"] is None
    assert status["last_success"] is not None
    assert status["priced_models"] == 150
    assert status["last_changes"]["added"] == {"count": 1, "sample": ["model-new"]}
    assert status["last_changes"]["removed"] == {"count": 1, "sample": ["model-1"]}
    assert status["last_changes"]["changed"] == {"count": 1, "sample": ["model-0"]}
    assert state["table"].lookup("x", "model-0").input_per_token == 9e-06
    assert json.loads(cache.read_text()) == new_raw


@pytest.mark.parametrize("response,error", [
    (httpx.Response(500), "HTTPStatusError"),
    (httpx.Response(200, text="not json"), "Error"),
    (httpx.Response(200, json=[1, 2, 3]), "ValueError"),
    (httpx.Response(200, json=raw_prices(MIN_PRICED_MODELS - 1)), "PricingRejected"),
    (httpx.Response(200, json=raw_prices(100, price=-1)), "PricingRejected"),  # all prices invalid
])
def test_bad_fetch_keeps_current_prices(tmp_path, response, error):
    old = PricingTable.from_raw(raw_prices(150), source="old")
    cache = tmp_path / "prices.json"
    r, state, _ = refresher([response], old, cache)
    status = asyncio.run(r.refresh_once())
    assert error in status["last_error"]
    assert status["last_success"] is None
    assert state["table"] is old
    assert not cache.exists()


def test_mass_removal_is_rejected():
    old = PricingTable.from_raw(raw_prices(400), source="old")
    r, state, _ = refresher([httpx.Response(200, json=raw_prices(200))], old)  # drops half
    status = asyncio.run(r.refresh_once())
    assert "would remove 200 of 400" in status["last_error"]
    assert state["table"] is old


def test_failure_then_recovery_clears_error():
    old = PricingTable.from_raw(raw_prices(150), source="old")
    r, state, _ = refresher([httpx.Response(503), httpx.Response(200, json=raw_prices(160))], old)
    assert asyncio.run(r.refresh_once())["last_error"]
    status = asyncio.run(r.refresh_once())
    assert status["last_error"] is None
    assert len(state["table"]) == 160


def test_run_once_fetches_once():
    r, _, calls = refresher([httpx.Response(200, json=raw_prices(150))], PricingTable.empty())
    asyncio.run(r.run(0))
    assert len(calls) == 1
    assert r.status["schedule"] == "once"


def test_run_on_interval_repeats():
    r, _, calls = refresher([httpx.Response(200, json=raw_prices(150))], PricingTable.empty())

    async def go():
        task = asyncio.create_task(r.run(interval_hours=0.01 / 3600))  # every 10 ms
        while len(calls) < 3:
            await asyncio.sleep(0.005)
        task.cancel()

    asyncio.run(asyncio.wait_for(go(), timeout=5))
    assert len(calls) >= 3
    assert r.status["schedule"].startswith("every")


def test_load_initial_prefers_valid_cache(tmp_path):
    vendored = tmp_path / "vendored.json"
    vendored.write_text(json.dumps(raw_prices(150)))
    cache = tmp_path / "cache.json"

    assert load_initial(vendored, cache).source == str(vendored)  # no cache yet
    cache.write_text(json.dumps(raw_prices(200)))
    assert len(load_initial(vendored, cache)) == 200
    cache.write_text(json.dumps(raw_prices(5)))  # too small: ignored
    assert len(load_initial(vendored, cache)) == 150
    cache.write_text("{corrupt")
    assert len(load_initial(vendored, cache)) == 150


# --- wired into Core ---------------------------------------------------------------


def core(tmp_path, refresh_hours, response):
    vendored = tmp_path / "vendored.json"
    vendored.write_text(json.dumps(raw_prices(150)))
    settings = Settings(
        DEFAULT_METHODOLOGY, DEFAULT_ESC, vendored, "https://example.org/donate", "s3cret",
        pricing_refresh_hours=refresh_hours, pricing_url=URL, pricing_cache_path=tmp_path / "cache.json",
    )
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda req: response))
    return create_app(settings, http=http)


def test_core_refreshes_once_at_startup(tmp_path):
    with TestClient(core(tmp_path, 0, httpx.Response(200, json=raw_prices(175)))) as c:
        deadline = time.time() + 5
        while c.get("/v1/pricing/status").json()["last_success"] is None and time.time() < deadline:
            time.sleep(0.01)
        status = c.get("/v1/pricing/status").json()
        assert status["schedule"] == "once"
        assert status["priced_models"] == 175
        assert c.get("/healthz").json()["priced_models"] == 175
        assert status["last_changes"]["added"]["count"] == 25


def test_core_refresh_off_and_admin_trigger(tmp_path):
    with TestClient(core(tmp_path, None, httpx.Response(200, json=raw_prices(175)))) as c:
        assert c.get("/v1/pricing/status").json()["schedule"] == "off"
        assert c.get("/healthz").json()["priced_models"] == 150
        assert c.post("/v1/admin/pricing/refresh").status_code == 401
        r = c.post("/v1/admin/pricing/refresh", headers={"X-Stardust-Admin-Token": "s3cret"})
        assert r.json()["priced_models"] == 175
        assert c.get("/healthz").json()["priced_models"] == 175
