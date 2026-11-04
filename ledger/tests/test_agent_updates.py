"""Agents re-assess rows each run (models.AGENT_REFRESH): for every tracker table and facilities, an
agent key may overwrite an allowed field on a row that isn't Reviewed or Published, with a source,
and the change is audited. Anything else is refused."""

import pytest

from conftest import ADMIN, add_facility, key_header, make_key
from ledger.models import AGENT_REFRESH, TABLES

SRC = "https://example.org/new-evidence"

# table: (scope, create path, create body, update path, allowed update, disallowed update, row id)
CASES = {
    "claims": ("tracker:write", "/v1/claims",
               {"facility_id": "DC-US-TX-0001", "claim_text": "\"zero water\"", "verdict": "Unverified",
                "source_url": "https://example.org/claim", "last_checked": "2026-10-01"},
               "/v1/claims/CLM-0001", {"verdict": "Contradicted by record", "reasoning": "Metered use found"},
               {"claim_text": "different words"}, "CLM-0001"),
    "water_ledger": ("tracker:write", "/v1/water",
                     {"facility_id": "DC-US-TX-0001", "facility_label": "Campus", "cooling_type": "Air / dry"},
                     "/v1/water", {"facility_id": "DC-US-TX-0001", "cooling_type": "Evaporative"},
                     {"facility_id": "DC-US-TX-0001", "facility_label": "Other"}, "DC-US-TX-0001"),
    "who_pays": ("tracker:write", "/v1/who-pays",
                 {"facility_id": "DC-US-TX-0001", "category": "Tax abatement", "value_usd": 100,
                  "source_url": "https://example.org/wp"},
                 "/v1/who-pays/WP-0001", {"value_usd": 250},
                 {"category": "Cash incentive or grant"}, "WP-0001"),
    "load_reality": ("tracker:write", "/v1/load-reality",
                     {"facility_id": "DC-US-TX-0001", "facility_label": "Campus", "land_control": 1},
                     "/v1/load-reality", {"facility_id": "DC-US-TX-0001", "land_control": 2},
                     {"facility_id": "DC-US-TX-0001", "facility_label": "Other"}, "DC-US-TX-0001"),
    "permit_checks": ("tracker:write", "/v1/permit-checks",
                      {"facility_id": "DC-US-TX-0001", "metric": "NOx", "limit_value": 100, "actual_value": 50},
                      "/v1/permit-checks/PVA-0001", {"actual_value": 120, "status": "Exceeds limit"},
                      {"limit_value": 200}, "PVA-0001"),
    "market_context": ("tracker:write", "/v1/market-context",
                       {"measure": "Example pipeline", "value": "479", "unit": "GW", "as_of": "2026-10-29"},
                       "/v1/market-context/1", {"value": "500"}, {"measure": "Other measure"}, "1"),
    "facilities": ("facilities:write", "/v1/facilities",
                   {"id_prefix": "DC", "name": "Second campus", "state": "US-TX", "country": "US",
                    "status": "Planned"},
                   "/v1/facilities/DC-US-TX-0002", {"status": "Under construction"},
                   {"name": "Renamed"}, "DC-US-TX-0002"),
}


def _send(client, path, body, headers):
    return (client.patch if path.count("/") > 2 else client.post)(path, json=body, headers=headers)


@pytest.fixture
def setup(client):
    def make(table):
        scope = CASES[table][0]
        agent = make_key(client, [scope], f"{table} agent")
        hdr = key_header(agent)
        add_facility(client, key_header(make_key(client, ["facilities:write"])), name="Campus")
        r = client.post(CASES[table][1], json=CASES[table][2], headers=hdr)
        assert r.status_code == 201, r.text
        return agent, hdr
    return make


def test_every_case_uses_allowed_fields():
    for table, case in CASES.items():
        allowed = set(case[4]) - {TABLES[table].pk}
        assert allowed <= AGENT_REFRESH[table], table
        assert not (set(case[5]) - {TABLES[table].pk}) & AGENT_REFRESH[table], table


@pytest.mark.parametrize("table", CASES)
def test_agent_can_reassess_an_allowed_field_and_it_is_audited(table, setup, client, pool):
    agent, hdr = setup(table)
    _, _, _, path, allowed, _, rid = CASES[table]
    r = _send(client, path, allowed | {"change_source_url": SRC}, hdr)
    assert r.status_code in (200, 201), r.text
    for k, v in allowed.items():
        assert str(r.json()[k]) == str(v)
    # Retrying the same update changes nothing and adds no audit row (idempotent).
    _send(client, path, allowed | {"change_source_url": SRC}, hdr)
    with pool.connection() as conn:
        rows = conn.execute("""SELECT before, after, source_url FROM audit_log
                               WHERE key_id = %s AND table_name = %s AND action = 'update' ORDER BY id""",
                            (agent["key_id"], table)).fetchall()
    assert len(rows) == 1, rows
    field = next(k for k in allowed if k != TABLES[table].pk)
    assert str(rows[0]["after"][field]) != str(rows[0]["before"][field])
    assert rows[0]["source_url"] == SRC


@pytest.mark.parametrize("table", CASES)
def test_agent_gets_409_on_a_field_it_may_not_overwrite(table, setup, client):
    _, hdr = setup(table)
    _, _, _, path, _, disallowed, _ = CASES[table]
    r = _send(client, path, disallowed | {"change_source_url": SRC}, hdr)
    assert r.status_code == 409, r.text
    assert r.json()["reason"] == "field_not_refreshable"


@pytest.mark.parametrize("table", CASES)
def test_agent_gets_409_on_reviewed_or_published_rows(table, setup, client):
    _, hdr = setup(table)
    _, _, _, path, allowed, _, rid = CASES[table]
    for status in ("Reviewed", "Published"):
        r = client.patch(f"/v1/admin/rows/{table}/{rid}", json={"review_status": status}, headers=ADMIN)
        assert r.status_code == 200, r.text
        r = _send(client, path, allowed | {"change_source_url": SRC}, hdr)
        assert r.status_code == 409 and r.json()["reason"] == "row_protected", r.text


@pytest.mark.parametrize("table", CASES)
def test_overwrite_needs_a_source(table, setup, client):
    _, hdr = setup(table)
    _, _, _, path, allowed, _, _ = CASES[table]
    r = _send(client, path, allowed, hdr)
    assert r.status_code == 422 and r.json()["field"] == "change_source_url", r.text
    # Bookkeeping alone (last checked, next step) needs no source.
    if "next_step" in AGENT_REFRESH[table]:
        first = {"next_step": "Ask the city"}
        if TABLES[table].client_pk:
            first["facility_id"] = "DC-US-TX-0001"
        assert _send(client, path, first, hdr).status_code in (200, 201)
        assert _send(client, path, first | {"next_step": "Ask the county"}, hdr).status_code in (200, 201)


def test_verdict_change_writes_the_claim_log(setup, client):
    agent, hdr = setup("claims")
    client.post("/v1/observations", json={"facility_id": "DC-US-TX-0001", "field_group": "Water: use & discharge",
                                          "field": "Metered use", "value": "40", "unit": "Mgal/yr",
                                          "source_url": "https://example.org/meter"},
                headers=key_header(make_key(client, ["observations:write"])))
    # Evidence observation IDs count as the source: each observation carries its own.
    r = client.patch("/v1/claims/CLM-0001", json={"verdict": "Contradicted by record",
                                                   "evidence_observation_ids": ["OBS-0001"]}, headers=hdr)
    assert r.status_code == 200, r.text
    log = client.get("/v1/claims/CLM-0001").json()["log"]
    assert [(e["old_verdict"], e["new_verdict"]) for e in log] == [("Unverified", "Contradicted by record")]
    assert agent["key_id"] in log[0]["change"]
    r = client.patch("/v1/claims/CLM-0001", json={"verdict": "Verified", "evidence_observation_ids": ["OBS-9999"]},
                     headers=hdr)
    assert r.status_code == 422


def test_agents_may_flag_conflicts_but_not_verify(setup, client):
    _, hdr = setup("claims")
    r = client.patch("/v1/claims/CLM-0001", json={"review_status": "Conflict"}, headers=hdr)
    assert r.status_code == 200 and r.json()["review_status"] == "Conflict"
    r = client.patch("/v1/claims/CLM-0001", json={"review_status": "Verified"}, headers=hdr)
    assert r.status_code == 409 and r.json()["reason"] == "protected_status"


def test_blank_never_erases_and_coordinates_move_with_their_source(client):
    fw = key_header(make_key(client, ["facilities:write"]))
    add_facility(client, fw, latitude=30.0, longitude=-97.0, location_precision="City", operator="Example")
    r = client.patch("/v1/facilities/DC-US-TX-0001", json={"operator": None}, headers=fw)
    assert r.status_code == 409  # unknown is not "remove"
    r = client.patch("/v1/facilities/DC-US-TX-0001",
                     json={"latitude": 30.2672, "longitude": -97.7431, "location_precision": "Exact site",
                           "coordinate_source": "https://example.org/permit"}, headers=fw)
    assert r.status_code == 200 and r.json()["latitude"] == 30.2672
