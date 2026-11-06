"""U.S. EIA Open Data API v2 connector: annual state generation mix and reported CO2 rate.

Two routes, both public with a free API key (https://www.eia.gov/opendata/register.php):

- ``electricity/electric-power-operational-data``: net generation by fuel, all sectors (Form EIA-923).
- ``electricity/state-electricity-profiles/emissions-by-state-by-fuel``: CO2 rate, lb/MWh, all fuels.

The key is sent as a query parameter but never written into a grid record or a source URL.
Tests replay recorded responses (tests/recorded/eia/); nothing here needs the network to be tested.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List, Optional, Sequence, Tuple

import httpx

from .grids import EIA_FUELS, LB_PER_MWH_TO_G_PER_KWH, computed_intensity, mix_from_generation

BASE_URL = "https://api.eia.gov/v2"
GENERATION_ROUTE = "electricity/electric-power-operational-data/data/"
CO2_ROUTE = "electricity/state-electricity-profiles/emissions-by-state-by-fuel/data/"
STATE_NAMES_URL = "https://www.eia.gov/electricity/state/"


class EIAError(RuntimeError):
    pass


def state_code(state: str) -> str:
    """"IN" or "US-IN" → "IN"."""
    s = state.strip().upper()
    if s.startswith("US-"):
        s = s[3:]
    if len(s) != 2 or not s.isalpha():
        raise ValueError(f"not a US state code: {state!r}")
    return s


def generation_params(state: str, year: int) -> List[Tuple[str, str]]:
    return [("frequency", "annual"), ("data[0]", "generation"), ("facets[location][]", state),
            ("facets[sectorid][]", "99"), ("start", str(year)), ("end", str(year)), ("length", "5000")]


def co2_params(state: str, year: int) -> List[Tuple[str, str]]:
    return [("frequency", "annual"), ("data[0]", "co2-rate-lbs-mwh"), ("facets[stateid][]", state),
            ("facets[fuelid][]", "ALL"), ("start", str(year)), ("end", str(year)), ("length", "5000")]


def _rows(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    if "error" in payload:
        err = payload["error"]
        if isinstance(err, dict):
            raise EIAError(f"EIA API error {err.get('code')}: {err.get('message')}")
        raise EIAError(f"EIA API error: {err}")
    try:
        return list(payload["response"]["data"])
    except (KeyError, TypeError) as e:
        raise EIAError("unexpected EIA API response (no response.data)") from e


def _float(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    return float(v)


def parse_generation(payload: Dict[str, Any], year: int) -> Tuple[Dict[str, float], Optional[float], str]:
    """Generation by EIA fuel ID (thousand MWh) for one year, the reported "ALL" total, and the unit."""
    gen: Dict[str, float] = {}
    total: Optional[float] = None
    unit = ""
    for row in _rows(payload):
        if str(row.get("period")) != str(year) or str(row.get("sectorid")) != "99":
            continue
        fuel, value = row.get("fueltypeid"), _float(row.get("generation"))
        unit = row.get("generation-units") or unit
        if fuel == "ALL":
            total = value
        elif fuel in EIA_FUELS and value is not None:
            gen[fuel] = value
    if not gen:
        raise EIAError(f"EIA returned no generation by fuel for {year}")
    return gen, total, unit


def parse_co2_rate(payload: Dict[str, Any], year: int) -> Optional[float]:
    """Reported CO2 rate (lb/MWh, all fuels) for the year, or None if EIA hasn't published it."""
    for row in _rows(payload):
        if str(row.get("period")) == str(year) and row.get("fuelid") == "ALL":
            return _float(row.get("co2-rate-lbs-mwh"))
    return None


def build_state_grid(state: str, year: int, generation_payload: Dict[str, Any],
                     co2_payload: Optional[Dict[str, Any]], retrieved: dt.date) -> Dict[str, Any]:
    """One grid record from the two EIA responses. Reported intensity wins over computed."""
    st = state_code(state)
    gen, total, unit = parse_generation(generation_payload, year)
    rate = parse_co2_rate(co2_payload, year) if co2_payload is not None else None
    computed = computed_intensity(gen)
    sources = [{"title": f"U.S. EIA, Electric Power Operations (Form EIA-923), {st} {year}, all sectors",
                "url": f"{BASE_URL}/{GENERATION_ROUTE}?{_query(generation_params(st, year))}",
                "retrieved": retrieved.isoformat()}]
    record: Dict[str, Any] = {
        "label": f"{st} {year}",
        "region_type": "state",
        "year": year,
        "mix": mix_from_generation(gen),
        "generation_by_fuel": {k: round(v, 3) for k, v in sorted(gen.items())},
        "generation_unit": unit,
        "computed_g_per_kwh": computed,
    }
    if total is not None:
        covered = sum(v for v in gen.values() if v > 0)
        record["generation_total"] = round(total, 3)
        record["generation_covered_pct"] = round(covered / total * 100, 1) if total > 0 else None
    if rate is not None:
        sources.append({"title": f"U.S. EIA, State Electricity Profiles: emissions by state, {st} {year}",
                        "url": f"{BASE_URL}/{CO2_ROUTE}?{_query(co2_params(st, year))}",
                        "retrieved": retrieved.isoformat()})
        record.update({"intensity_g_per_kwh": round(rate * LB_PER_MWH_TO_G_PER_KWH, 1), "intensity_basis": "reported",
                       "reported_co2_lb_per_mwh": rate, "confidence": "reported"})
    else:
        record.update({"intensity_g_per_kwh": computed, "intensity_basis": "computed", "confidence": "estimated",
                       "rationale": "EIA has not reported a CO2 rate for this year; computed from the generation "
                                    "mix with EIA combustion factors (coal 1043, oil 1088, gas 440 g/kWh)."})
    record["sources"] = sources
    record["as_of"] = f"{year}-12-31"
    return record


def _query(params: Sequence[Tuple[str, str]]) -> str:
    return str(httpx.QueryParams(list(params)))


def fetch_state_grid(state: str, year: int, api_key: str, client: Optional[httpx.Client] = None,
                     base_url: str = BASE_URL, today: Optional[dt.date] = None) -> Dict[str, Any]:
    """Fetch and build one state grid record. ``client`` lets tests replay recorded responses."""
    st = state_code(state)
    own = client is None
    client = client or httpx.Client(timeout=30)
    try:
        def get(route: str, params: List[Tuple[str, str]]) -> Dict[str, Any]:
            r = client.get(f"{base_url}/{route}", params=params + [("api_key", api_key)])
            try:
                payload = r.json()
            except ValueError as e:
                raise EIAError(f"EIA API returned HTTP {r.status_code} without JSON") from e
            if r.status_code >= 400 and "error" not in payload:
                raise EIAError(f"EIA API returned HTTP {r.status_code}")
            return payload

        gen = get(GENERATION_ROUTE, generation_params(st, year))
        co2 = get(CO2_ROUTE, co2_params(st, year))
    finally:
        if own:
            client.close()
    return build_state_grid(st, year, gen, co2, today or dt.date.today())
