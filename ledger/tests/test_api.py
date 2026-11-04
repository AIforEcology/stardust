"""The HTTP API: public reads, keyed writes, idempotency, protection rules and the admin guard."""

from conftest import ADMIN, add_facility, key_header, make_key

OBS = {"facility_id": "DC-US-TX-0001", "field_group": "Status & size", "field": "Capacity", "value": "300",
       "unit": "MW", "source_url": "https://example.org/a", "source_title": "Example", "source_type": "News",
       "source_date": "2026-10-01", "method": "Reported", "confidence": 0.6}


def test_public_endpoints_need_no_key(client, keys):
    add_facility(client, keys["facilities:write"], latitude=30.2672, longitude=-97.7431)
    for path in ("/healthz", "/v1/facilities", "/v1/facilities/DC-US-TX-0001", "/v1/observations", "/v1/claims",
                 "/v1/water", "/v1/who-pays", "/v1/load-reality", "/v1/permit-checks", "/v1/sources",
                 "/v1/facilities?format=geojson"):
        r = client.get(path)
        assert r.status_code == 200, (path, r.text)
    body = client.get("/v1/facilities").json()
    assert body["total"] == 1 and body["attribution"]["credits"]
    geo = client.get("/v1/facilities?format=geojson").json()
    assert geo["type"] == "FeatureCollection"
    assert geo["features"][0]["geometry"]["coordinates"] == [-97.7431, 30.2672]


def test_writes_refuse_missing_wrong_revoked_and_wrong_scope_keys(client):
    body = {"id_prefix": "DC", "name": "x", "state": "US-TX", "country": "US"}
    assert client.post("/v1/facilities", json=body).status_code == 401
    assert client.post("/v1/facilities", json=body, headers={"X-Ledger-Key": "lk_nope_nope"}).status_code == 401
    k = make_key(client, ["facilities:write"])
    forged = k["key"][:-2] + ("AA" if not k["key"].endswith("AA") else "BB")
    assert client.post("/v1/facilities", json=body, headers={"X-Ledger-Key": forged}).status_code == 401
    obs_key = key_header(make_key(client, ["observations:write"]))
    r = client.post("/v1/facilities", json=body, headers=obs_key)
    assert r.status_code == 403 and r.json()["reason"] == "forbidden"
    assert client.post("/v1/facilities", json=body, headers=key_header(k)).status_code == 201
    assert client.post(f"/v1/admin/keys/{k['key_id']}/revoke", headers=ADMIN).status_code == 200
    r = client.post("/v1/facilities", json=body, headers=key_header(k))
    assert r.status_code == 401 and r.json()["reason"] == "key_revoked"
    # Ordinary keys, even with admin scope, never reach admin routes through the proxy.
    adm = key_header(make_key(client, ["admin"]))
    assert client.get("/v1/admin/keys", headers=adm).status_code == 200
    assert client.get("/v1/admin/keys", headers=adm | {"X-Forwarded-For": "203.0.113.9"}).status_code == 404
    assert client.get("/v1/admin/keys", headers=ADMIN | {"X-Forwarded-For": "203.0.113.9"}).status_code == 404
    assert client.get("/v1/admin/keys", headers={"X-Ledger-Admin-Token": "wrong"}).status_code == 401


def test_keys_are_stored_as_hashes(client, pool):
    k = make_key(client, ["runlog:write"])
    with pool.connection() as conn:
        row = conn.execute("SELECT key_hash FROM api_keys WHERE key_id = %s", (k["key_id"],)).fetchone()
    assert row["key_hash"] != k["key"] and len(row["key_hash"]) == 64
    assert "key" not in client.get("/v1/admin/keys", headers=ADMIN).json()["keys"][0]


def test_server_assigns_facility_ids(client, keys):
    a = add_facility(client, keys["facilities:write"])
    b = add_facility(client, keys["facilities:write"], id_prefix="PWR", name="Plant")
    assert (a["facility_id"], b["facility_id"]) == ("DC-US-TX-0001", "PWR-US-TX-0001")
    r = client.post("/v1/facilities", json={"id_prefix": "DC", "facility_id": "DC-US-TX-0999", "name": "x",
                                            "state": "US-TX", "country": "US"}, headers=keys["facilities:write"])
    assert r.status_code == 422  # clients can't choose IDs


def test_retried_writes_create_one_row(client, keys):
    fw = keys["facilities:write"]
    first = client.post("/v1/facilities", json={"id_prefix": "DC", "name": "x", "state": "US-TX", "country": "US",
                                                "idempotency_key": "agent-run-1-site-1"}, headers=fw)
    again = client.post("/v1/facilities", json={"id_prefix": "DC", "name": "x", "state": "US-TX", "country": "US",
                                                "idempotency_key": "agent-run-1-site-1"}, headers=fw)
    assert first.status_code == 201 and again.status_code == 200
    assert first.json()["facility_id"] == again.json()["facility_id"] and again.json()["idempotent_replay"]
    assert client.get("/v1/facilities").json()["total"] == 1

    ow = keys["observations:write"]
    o1 = client.post("/v1/observations", json=OBS | {"idempotency_key": "obs-1"}, headers=ow)
    o2 = client.post("/v1/observations", json=OBS | {"idempotency_key": "obs-1"}, headers=ow)
    o3 = client.post("/v1/observations", json=OBS, headers=ow)  # no key: the natural key matches
    assert o1.status_code == 201 and o2.status_code == 200 and o3.status_code == 200
    assert o1.json()["obs_id"] == o2.json()["obs_id"] == o3.json()["obs_id"] == "OBS-0001"
    assert client.get("/v1/observations").json()["total"] == 1

    rw = keys["runlog:write"]
    run = {"run_date": "2026-11-03", "region": "US", "searches_run": 9, "idempotency_key": "run-2026-11-03"}
    assert client.post("/v1/run-log", json=run, headers=rw).status_code == 201
    assert client.post("/v1/run-log", json=run, headers=rw).status_code == 200


def test_observations_are_append_only_through_the_api(client, keys):
    add_facility(client, keys["facilities:write"])
    obs = client.post("/v1/observations", json=OBS, headers=keys["observations:write"]).json()
    oid = obs["obs_id"]
    hdr = keys["observations:write"]
    assert client.put(f"/v1/observations/{oid}", json={"value": "1"}, headers=hdr).status_code in (404, 405)
    assert client.patch(f"/v1/observations/{oid}", json={"value": "1"}, headers=hdr).status_code in (404, 405)
    assert client.delete(f"/v1/observations/{oid}", headers=hdr).status_code in (404, 405)
    assert client.delete("/v1/observations", headers=hdr).status_code == 405
    # A correction is a new row pointing at the old one; the latest view drops the superseded row.
    fix = client.post("/v1/observations", json=OBS | {"value": "350", "supersedes": oid}, headers=hdr)
    assert fix.status_code == 201 and fix.json()["supersedes"] == oid
    latest = client.get("/v1/facilities/DC-US-TX-0001").json()["latest_observations"]
    assert [o["value"] for o in latest] == ["350"]
    assert client.get("/v1/observations?facility_id=DC-US-TX-0001").json()["total"] == 2
    # Admin may change review_status and notes, nothing else.
    r = client.patch(f"/v1/admin/rows/observations/{oid}", json={"value": "1"}, headers=ADMIN)
    assert r.status_code == 409
    r = client.patch(f"/v1/admin/rows/observations/{oid}", json={"review_status": "Verified"}, headers=ADMIN)
    assert r.status_code == 200 and r.json()["review_status"] == "Verified"


def test_ordinary_writes_only_fill_blank_fields(client, keys):
    fw = keys["facilities:write"]
    add_facility(client, fw, operator="Example Cloud")
    r = client.patch("/v1/facilities/DC-US-TX-0001", json={"utility": "Example Utility"}, headers=fw)
    assert r.status_code == 200 and r.json()["changed"]
    r = client.patch("/v1/facilities/DC-US-TX-0001", json={"utility": "Example Utility"}, headers=fw)
    assert r.status_code == 200 and not r.json()["changed"]  # same value: a no-op, safe to retry
    r = client.patch("/v1/facilities/DC-US-TX-0001", json={"operator": "Someone Else"}, headers=fw)
    assert r.status_code == 409 and r.json()["reason"] == "field_filled" and r.json()["fields"] == ["operator"]
    # The same rule holds for tracker rows keyed by facility.
    tw = keys["tracker:write"]
    assert client.post("/v1/water", json={"facility_id": "DC-US-TX-0001", "cooling_type": "Air / dry"},
                       headers=tw).status_code == 201
    r = client.post("/v1/water", json={"facility_id": "DC-US-TX-0001", "cooling_type": "Evaporative"}, headers=tw)
    assert r.status_code == 409
    r = client.post("/v1/water", json={"facility_id": "DC-US-TX-0001", "supplier_name": "Example Water"}, headers=tw)
    assert r.status_code == 201 and r.json()["completeness"] == 2


def test_reviewed_and_published_rows_need_admin(client, keys, pool):
    fw, tw = keys["facilities:write"], keys["tracker:write"]
    add_facility(client, fw)
    claim = client.post("/v1/claims", json={"facility_id": "DC-US-TX-0001", "claim_text": "\"zero water\"",
                                            "verdict": "Unverified", "source_url": "https://example.org/c"},
                        headers=tw).json()
    assert claim["claim_id"] == "CLM-0001"
    # Only admin can mark a row Reviewed or Published.
    r = client.patch("/v1/claims/CLM-0001", json={"review_status": "Reviewed"}, headers=tw)
    assert r.status_code == 409
    for table, rid in (("claims", "CLM-0001"), ("facilities", "DC-US-TX-0001")):
        r = client.patch(f"/v1/admin/rows/{table}/{rid}", json={"review_status": "Published"}, headers=ADMIN)
        assert r.status_code == 200, r.text
    r = client.patch("/v1/claims/CLM-0001", json={"next_step": "Ask the city"}, headers=tw)
    assert r.status_code == 409 and r.json()["reason"] == "row_protected"
    r = client.patch("/v1/facilities/DC-US-TX-0001", json={"utility": "X"}, headers=fw)
    assert r.status_code == 409 and r.json()["reason"] == "row_protected"
    r = client.post("/v1/facilities", json={"id_prefix": "DC", "name": "y", "state": "US-TX", "country": "US",
                                            "review_status": "Reviewed"}, headers=fw)
    assert r.status_code == 409
    # Admin edits go through, and every one is audited with who, before and after.
    r = client.patch("/v1/admin/rows/claims/CLM-0001", json={"verdict": "Verified"}, headers=ADMIN)
    assert r.status_code == 200 and r.json()["verdict"] == "Verified"
    with pool.connection() as conn:
        rows = conn.execute("""SELECT key_id, before ->> 'verdict' AS old, after ->> 'verdict' AS new
                               FROM audit_log WHERE table_name = 'claims' AND action = 'update'
                               ORDER BY id DESC LIMIT 1""").fetchone()
    assert rows == {"key_id": "admin-token", "old": "Unverified", "new": "Verified"}


def test_claim_log_and_trackers(client, keys):
    tw = keys["tracker:write"]
    add_facility(client, keys["facilities:write"])
    client.post("/v1/claims", json={"claim_text": "x", "verdict": "Unverified"}, headers=tw)
    r = client.post("/v1/claims/CLM-0001/log", json={"log_date": "2026-11-03", "change": "first check",
                                                     "new_verdict": "Unverified", "idempotency_key": "l1"}, headers=tw)
    assert r.status_code == 201
    assert client.post("/v1/claims/CLM-0001/log", json={"log_date": "2026-11-03", "change": "first check",
                                                        "idempotency_key": "l1"}, headers=tw).status_code == 200
    assert len(client.get("/v1/claims/CLM-0001").json()["log"]) == 1
    assert client.post("/v1/claims", json={"claim_text": "x", "verdict": "Probably"}, headers=tw).status_code == 422
    wp = client.post("/v1/who-pays", json={"facility_id": "DC-US-TX-0001", "category": "Tax abatement",
                                           "value_usd": "Not quantified"}, headers=tw).json()
    assert wp["item_id"] == "WP-0001" and wp["value_usd"] is None
    pva = client.post("/v1/permit-checks", json={"metric": "NOx", "limit_value": 100, "actual_value": 2500},
                      headers=tw).json()
    assert pva["check_id"] == "PVA-0001" and pva["gap"] == 2400
    lr = client.post("/v1/load-reality", json={"facility_id": "DC-US-TX-0001", "stated_load_mw": 100,
                                               "land_control": 1, "local_approvals": 1, "power_secured": 0,
                                               "equipment_financing": 0, "construction": 0, "sponsor_tenant": 1},
                     headers=tw).json()
    assert (lr["deliverability_score"], lr["tier"]) == (25, "Early stage")
    assert client.get("/v1/load-reality").json()["items"][0]["tier"] == "Early stage"


def test_near_and_bbox_queries(client, keys):
    fw = keys["facilities:write"]
    add_facility(client, fw, name="Austin", latitude=30.2672, longitude=-97.7431)        # DC-US-TX-0001
    add_facility(client, fw, name="Round Rock", latitude=30.5083, longitude=-97.6789)    # ~27 km north
    add_facility(client, fw, name="Dallas", latitude=32.7767, longitude=-96.7970)        # ~290 km
    add_facility(client, fw, name="No coordinates")
    r = client.get("/v1/facilities?near=30.27,-97.74&km=50").json()
    assert [f["name"] for f in r["items"]] == ["Austin", "Round Rock"]
    assert r["items"][0]["distance_km"] < 1 < r["items"][1]["distance_km"] < 50
    assert [f["name"] for f in client.get("/v1/facilities?near=30.27,-97.74&km=5").json()["items"]] == ["Austin"]
    box = client.get("/v1/facilities?bbox=-98,32,-96,33").json()
    assert [f["name"] for f in box["items"]] == ["Dallas"]
    assert client.get("/v1/facilities?near=abc").status_code == 422
    assert client.get("/v1/facilities?q=round").json()["items"][0]["name"] == "Round Rock"
    assert client.get("/v1/facilities?state=TX&limit=2").json()["next_offset"] == 2


def test_names_links_and_detail(client, keys):
    fw = keys["facilities:write"]
    add_facility(client, fw, name="Campus")
    add_facility(client, fw, id_prefix="PWR", name="Plant")
    assert client.post("/v1/facilities/DC-US-TX-0001/names", json={"name": "Project X", "name_type": "Code name"},
                       headers=fw).status_code == 201
    assert client.post("/v1/facilities/DC-US-TX-0001/names", json={"name": "Project X"}, headers=fw).status_code == 200
    r = client.post("/v1/facilities/PWR-US-TX-0001/links", json={"to_facility_id": "DC-US-TX-0001",
                                                                 "link_status": "confirmed", "confidence": 0.9},
                    headers=fw)
    assert r.status_code == 201
    d = client.get("/v1/facilities/DC-US-TX-0001").json()
    assert [n["name"] for n in d["names"]] == ["Project X"]
    assert d["links"][0]["from_facility_id"] == "PWR-US-TX-0001" and d["links"][0]["link_status"] == "confirmed"
    assert client.get("/v1/facilities?q=project x").json()["total"] == 1
    assert client.get("/v1/facilities/DC-US-TX-0404").status_code == 404


def test_rate_limit(settings, pool):
    from fastapi.testclient import TestClient

    from ledger.api import create_app
    from ledger.config import Settings
    s = Settings(**{**settings.__dict__, "reads_per_ip_per_minute": 3})
    with TestClient(create_app(s, pool=pool)) as c:
        codes = [c.get("/v1/facilities").status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
