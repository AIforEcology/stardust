"""The corrections triage agent writes the Corrections tab with a corrections:write key (not admin), and
an admin can add a scope to an existing key without issuing a new secret."""

from conftest import ADMIN, key_header, make_key

SUB = {"correction_id": "s-7f3a", "received": "2026-11-01", "submitter_role": "Resident",
       "record_id": "OBS-0001", "facility_id": "DC-US-TX-0001", "issue": "Wrong county",
       "proposed": "Hood County", "evidence_urls": ["https://example.org/a", "https://example.org/b,c"]}
SRC = "https://example.org/new-evidence"


def test_corrections_need_their_scope_and_are_not_public(client, keys):
    assert client.get("/v1/corrections").status_code == 401
    for scope, hdr in keys.items():
        assert client.get("/v1/corrections", headers=hdr).status_code == 403, scope
        assert client.post("/v1/corrections", json=SUB, headers=hdr).status_code == 403, scope


def test_triage_agent_files_assesses_and_copies_a_decision(client, pool):
    hdr = key_header(make_key(client, ["corrections:write"], "corrections triage"))
    r = client.post("/v1/corrections", json=SUB | {"idempotency_key": "triage-2026-11-07-1"}, headers=hdr)
    assert r.status_code == 201, r.text
    assert r.json()["evidence_urls"] == SUB["evidence_urls"]
    # A retry changes nothing.
    r = client.post("/v1/corrections", json=SUB | {"idempotency_key": "triage-2026-11-07-1"}, headers=hdr)
    assert r.status_code == 200 and r.json()["idempotent_replay"] is True
    # Assessment, then the person's decision, are filled in.
    r = client.patch("/v1/corrections/s-7f3a", json={"proposed_assessment": "Cannot verify."}, headers=hdr)
    assert r.status_code == 200 and r.json()["changed"] is True, r.text
    r = client.patch("/v1/corrections/s-7f3a", json={"decision": "Accepted", "decision_date": "2026-11-05",
                                                      "reviewer_note": "Checked the deed"}, headers=hdr)
    assert r.status_code == 200, r.text
    # Re-assessing needs a source; the decision is fill-only.
    r = client.patch("/v1/corrections/s-7f3a", json={"proposed_assessment": "Evidence supports it."}, headers=hdr)
    assert r.status_code == 422 and r.json()["field"] == "change_source_url"
    r = client.patch("/v1/corrections/s-7f3a", json={"proposed_assessment": "Evidence supports it.",
                                                      "change_source_url": SRC}, headers=hdr)
    assert r.status_code == 200, r.text
    r = client.patch("/v1/corrections/s-7f3a", json={"decision": "Rejected"}, headers=hdr)
    assert r.status_code == 409 and r.json()["reason"] == "field_not_refreshable"
    # Reads, with the key.
    r = client.get("/v1/corrections?decision=Accepted", headers=hdr)
    assert r.status_code == 200 and r.json()["total"] == 1
    assert client.get("/v1/corrections/s-7f3a", headers=hdr).json()["decision"] == "Accepted"
    assert client.get("/v1/corrections/nope", headers=hdr).status_code == 404
    # Contact details have no column, and an email can't hide in the submitter role.
    assert client.post("/v1/corrections", json={"correction_id": "s-1", "email": "a@b.org"}, headers=hdr).status_code == 422
    assert client.post("/v1/corrections", json={"correction_id": "s-2", "submitter_role": "a@b.org"},
                       headers=hdr).status_code == 422
    with pool.connection() as conn:
        n = conn.execute("SELECT count(*) AS n FROM audit_log WHERE table_name = 'corrections' "
                         "AND action = 'update'").fetchone()["n"]
    assert n >= 3


def test_agents_cannot_set_reviewed_on_a_correction(client):
    hdr = key_header(make_key(client, ["corrections:write"]))
    r = client.post("/v1/corrections", json=SUB | {"review_status": "Reviewed"}, headers=hdr)
    assert r.status_code == 409 and r.json()["reason"] == "protected_status"


def test_source_registry_stays_admin_only(client, keys):
    for hdr in keys.values():
        assert client.post("/v1/sources", json={"source": "X"}, headers=hdr).status_code == 405


def test_admin_adds_a_scope_to_an_existing_key(client, pool):
    k = make_key(client, ["tracker:write", "observations:write", "runlog:write"], "claims checker")
    hdr = key_header(k)
    body = {"id_prefix": "DC", "name": "New site", "state": "US-TX", "country": "US"}
    assert client.post("/v1/facilities", json=body, headers=hdr).status_code == 403
    r = client.post(f"/v1/admin/keys/{k['key_id']}/scopes", json={"add": ["facilities:write"]}, headers=ADMIN)
    assert r.status_code == 200, r.text
    assert r.json()["scopes"] == ["facilities:write", "observations:write", "runlog:write", "tracker:write"]
    assert client.post("/v1/facilities", json=body, headers=hdr).status_code == 201  # same secret works
    r = client.post(f"/v1/admin/keys/{k['key_id']}/scopes", json={"remove": ["facilities:write"]}, headers=ADMIN)
    assert "facilities:write" not in r.json()["scopes"]
    # Bad input, unknown key, emptying a key, and calls through the proxy are refused.
    assert client.post(f"/v1/admin/keys/{k['key_id']}/scopes", json={"add": ["root"]}, headers=ADMIN).status_code == 422
    assert client.post(f"/v1/admin/keys/{k['key_id']}/scopes", json={}, headers=ADMIN).status_code == 422
    assert client.post("/v1/admin/keys/nope/scopes", json={"add": ["admin"]}, headers=ADMIN).status_code == 404
    assert client.post(f"/v1/admin/keys/{k['key_id']}/scopes",
                       json={"remove": ["tracker:write", "observations:write", "runlog:write"]},
                       headers=ADMIN).status_code == 422
    assert client.post(f"/v1/admin/keys/{k['key_id']}/scopes", json={"add": ["admin"]},
                       headers=ADMIN | {"X-Forwarded-For": "1.2.3.4"}).status_code == 404
    assert client.post(f"/v1/admin/keys/{k['key_id']}/scopes", json={"add": ["admin"]}, headers=hdr).status_code == 403
    with pool.connection() as conn:
        n = conn.execute("SELECT count(*) AS n FROM audit_log WHERE action = 'change_scopes'").fetchone()["n"]
    assert n == 2
