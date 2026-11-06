"""EIA API v2 connector, against responses recorded from api.eia.gov on 2026-11-05."""

import datetime as dt

import httpx
import pytest

from dci_testdata import recorded
from stardust_intel.eia import CO2_ROUTE, GENERATION_ROUTE, EIAError, build_state_grid, fetch_state_grid, state_code
from stardust_intel.grids import validate_grid

TODAY = dt.date(2026, 11, 5)


def test_indiana_2025_reported():
    g = build_state_grid("IN", 2025, recorded("generation-IN-2025.json"), recorded("co2-rate-IN-2024-2025.json"),
                         TODAY)
    # EIA reports 1,414 lb/MWh for Indiana in 2025 → 641.4 g/kWh.
    assert g["intensity_basis"] == "reported"
    assert g["confidence"] == "reported"
    assert g["intensity_g_per_kwh"] == pytest.approx(641.4)
    assert g["mix"]["FPP"] == pytest.approx(47.9)  # coal, recorded as FPP like Core's methodology
    assert sum(g["mix"].values()) == pytest.approx(100, abs=0.5)
    assert validate_grid("US-IN", g) == []


def test_tennessee_matches_spec_and_skips_pumped_storage():
    g = build_state_grid("US-TN", 2024 + 1, recorded("generation-TN-2025.json"),
                         recorded("co2-rate-TN-2024-2025.json"), TODAY)
    assert g["intensity_g_per_kwh"] == pytest.approx(371.0)  # 818 lb/MWh
    assert g["mix"]["NPP"] == pytest.approx(38.6)
    assert "HPS" not in g["generation_by_fuel"]  # pumped storage is net negative and left out
    # The spec's Tennessee figure: EIA 2024, 804 lb/MWh → 365 g/kWh.
    rate_2024 = [r for r in recorded("co2-rate-TN-2024-2025.json")["response"]["data"] if r["period"] == "2024"][0]
    assert float(rate_2024["co2-rate-lbs-mwh"]) * 0.45359237 == pytest.approx(365, abs=0.5)


def test_computed_when_not_reported_is_estimated():
    g = build_state_grid("IN", 2024, recorded("generation-IN-2024.json"), None, TODAY)
    assert g["intensity_basis"] == "computed"
    assert g["confidence"] == "estimated"
    assert g["rationale"]
    # The spec's check: the computed figure lands within 4% of EIA's reported 2024 figure (1,393 lb/MWh = 632 g/kWh).
    assert g["intensity_g_per_kwh"] == pytest.approx(632, rel=0.04)
    assert validate_grid("US-IN", g) == []


def test_fetch_sends_key_but_never_records_it():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        assert request.url.params["api_key"] == "test-key"
        if request.url.path.endswith(GENERATION_ROUTE):
            assert request.url.params["facets[location][]"] == "TN"
            return httpx.Response(200, json=recorded("generation-TN-2025.json"))
        assert request.url.path.endswith(CO2_ROUTE)
        return httpx.Response(200, json=recorded("co2-rate-TN-2024-2025.json"))

    g = fetch_state_grid("TN", 2025, "test-key", client=httpx.Client(transport=httpx.MockTransport(handler)),
                         today=TODAY)
    assert len(seen) == 2
    assert g["intensity_g_per_kwh"] == pytest.approx(371.0)
    assert "test-key" not in str(g)
    assert all(s["retrieved"] == "2026-11-05" for s in g["sources"])


def test_api_error_is_raised():
    client = httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(429, json=recorded("error-over-rate-limit.json"))))
    with pytest.raises(EIAError, match="OVER_RATE_LIMIT"):
        fetch_state_grid("NY", 2025, "k", client=client)


def test_no_data_for_year():
    with pytest.raises(EIAError, match="no generation"):
        build_state_grid("IN", 2030, recorded("generation-IN-2025.json"), None, TODAY)


@pytest.mark.parametrize("raw,code", [("in", "IN"), ("US-TN", "TN"), (" ny ", "NY")])
def test_state_code(raw, code):
    assert state_code(raw) == code


def test_bad_state_code():
    with pytest.raises(ValueError):
        state_code("Indiana")
