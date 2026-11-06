"""Shared test data. The ledger here is synthetic (the live ledger is empty until the sheet import);
its responses follow the ledger API's shapes, which test_ledger_db.py checks against the real app."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
import pytest

from stardust_intel.ledger import ApiLedger

HERE = Path(__file__).parent
RECORDED = HERE / "recorded"
INTEL = HERE.parent
REPO = INTEL.parent
METHODOLOGY = REPO / "schema" / "factors" / "methodology-v0.2.json"


def recorded(name: str) -> Dict[str, Any]:
    return json.loads((RECORDED / "eia" / name).read_text())


def _facility(fid: str, name: str, state: str, tenant: Optional[str], mw: Optional[float] = None,
              operator: Optional[str] = None) -> Dict[str, Any]:
    return {"facility_id": fid, "name": name, "facility_type": "Data center", "operator": operator, "tenant": tenant,
            "status": "Operational", "city": None, "county": None, "state": state, "country": "US",
            "grid_region": None, "utility": None, "operational_mw": mw, "planned_mw": None, "eia_plant_ids": [],
            "latitude": None, "longitude": None, "review_status": "Needs review"}


# Synthetic facilities. Names are chosen to exercise the allocations file's searches, not real records.
FACILITIES: List[Dict[str, Any]] = [
    _facility("DC-US-IN-0001", "Project Rainier New Carlisle", "US-IN", "Anthropic", 2200, "Amazon"),
    _facility("DC-US-TN-0001", "Colossus 1", "US-TN", "Anthropic", 300, "SpaceXAI"),
    _facility("PWR-US-TN-0001", "Colossus 1 turbines", "US-TN", None, operator="SpaceXAI"),
    _facility("DC-US-MS-0001", "Project Rainier Mississippi", "US-MS", "Anthropic", None, "Amazon"),
    _facility("DC-US-NY-0001", "Lake Mariner", "US-NY", "Core42; Anthropic", 42, "TeraWulf"),
    _facility("DC-US-TX-0001", "Stargate Abilene", "US-TX", "OpenAI; Oracle", 300, "Crusoe"),
    _facility("DC-US-VA-0001", "Unallocated campus", "US-VA", "Anthropic", 50),
]
LINKS = [{"id": 1, "from_facility_id": "PWR-US-TN-0001", "to_facility_id": "DC-US-TN-0001", "relation": "serves",
          "link_status": "confirmed", "confidence": 0.8, "source_url": "https://example.org/permit"}]
OBSERVATIONS = [{"obs_id": "OBS-0001", "facility_id": "DC-US-TN-0001", "field_group": "Power: on-site",
                 "field": "Gas turbines", "value": "15 turbines", "unit": None,
                 "source_url": "https://example.org/air-permit", "source_date": "2026-05-01", "confidence": 0.9,
                 "review_status": "Needs review", "date_found": "2026-10-30"}]


def _search(q: Optional[str], state: Optional[str]) -> List[Dict[str, Any]]:
    out = []
    for f in FACILITIES:
        if state and f["state"] != state:
            continue
        hay = " ".join(str(f.get(k) or "") for k in ("name", "operator", "tenant", "facility_id")).lower()
        if q and q.lower() not in hay:
            continue
        out.append(f)
    return out


def ledger_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.startswith("/ledger"):
        path = path[len("/ledger"):]
    if path == "/healthz":
        return httpx.Response(200, json={"ok": True, "service": "ledger", "version": "0.1.0", "schema_version": 2,
                                         "facilities": len(FACILITIES)})
    if path == "/v1/facilities":
        p = request.url.params
        items = _search(p.get("q"), p.get("state"))
        limit, offset = int(p.get("limit", 100)), int(p.get("offset", 0))
        page = items[offset:offset + limit]
        nxt = offset + limit if offset + limit < len(items) else None
        return httpx.Response(200, json={"items": page, "total": len(items), "limit": limit, "offset": offset,
                                         "next_offset": nxt})
    if path.startswith("/v1/facilities/"):
        fid = path.rsplit("/", 1)[-1]
        f = next((x for x in FACILITIES if x["facility_id"] == fid), None)
        if f is None:
            return httpx.Response(404, json={"error": "not_found"})
        links = [x for x in LINKS if fid in (x["from_facility_id"], x["to_facility_id"])]
        return httpx.Response(200, json={**f, "names": [], "aliases": [], "links": links,
                                         "latest_observations": [o for o in OBSERVATIONS if o["facility_id"] == fid]})
    return httpx.Response(404)


@pytest.fixture
def ledger() -> ApiLedger:
    return ApiLedger("https://ledger.test/ledger", client=httpx.Client(transport=httpx.MockTransport(ledger_handler)))


@pytest.fixture
def core_cfg() -> Dict[str, Any]:
    return json.loads(METHODOLOGY.read_text())
