"""The one-time sheet import, on a small synthetic CSV set (tests/fixtures/sheet; no real sheet data)."""

import json
import sys
from pathlib import Path

from conftest import ADMIN, FIXTURES, add_facility, key_header, make_key
from ledger import db, importer


def _load_direct(pool):
    batches, report = importer.prepare(FIXTURES)
    with db.transaction(pool, admin_key_id="import-test") as conn:
        for b in batches:
            importer.record(report, b, importer.load(conn, b.table, b.mode, b.rows))
        db.sync_counters(conn)
    return report


def test_prepare_reports_problems_without_a_database():
    _, report = importer.prepare(FIXTURES)
    dups = {(d["tab"], d["id"]) for d in report.duplicate_ids}
    assert ("US Master List", "DC-US-TX-0003") in dups         # same ID twice in one tab
    assert ("US Master List", "DC-US-OH-0001") in dups         # collides with a curated ID
    assert [o["id"] for o in report.outside_us] == ["DC-US-CA-0001"]
    assert [b["id"] for b in report.bad_confidence] == ["OBS-0002"]
    rejected = {(r["tab"], r["id"]) for r in report.rejected}
    assert ("Observations", "OBS-0003") in rejected            # field group not one of the nine
    assert ("US Master List", "BAD-ID") in rejected
    assert report.dropped_columns["Corrections"] == ["Submitter email"]  # contact details never read
    assert report.missing_tabs == []


def test_import_round_trips_with_ids_unchanged(client, pool):
    report = _load_direct(pool)
    assert report.loaded["facilities"] == 6  # 3 curated + 2 new Master List sites + 1 merged into a curated one
    ids = sorted(f["facility_id"] for f in client.get("/v1/facilities").json()["items"])
    assert ids == ["DC-US-CA-0001", "DC-US-OH-0001", "DC-US-TX-0001", "DC-US-TX-0003", "PWR-US-TX-0001"]
    obs = sorted(o["obs_id"] for o in client.get("/v1/observations").json()["items"])
    assert obs == ["OBS-0001", "OBS-0002", "OBS-0004"]

    austin = client.get("/v1/facilities/DC-US-TX-0001").json()
    assert austin["aliases"] == ["DC-US-TX-0002"]
    assert client.get("/v1/facilities/DC-US-TX-0002").json()["facility_id"] == "DC-US-TX-0001"
    # Curated values win; the Master List only fills blanks.
    assert austin["operator"] == "Example Cloud" and austin["compute_atlas_id"] == "example-austin-tx"
    assert float(austin["planned_mw"]) == 1000 and austin["eia_plant_ids"] == ["123", "456"]
    assert {n["name"] for n in austin["names"]} == {"Project Example", "Example Holdings LLC"}
    assert austin["links"][0]["from_facility_id"] == "PWR-US-TX-0001"
    assert austin["links"][0]["link_status"] == "confirmed"

    claim = client.get("/v1/claims/CLM-0001").json()
    assert claim["evidence_observation_ids"] == ["OBS-0001", "OBS-0002"] and len(claim["log"]) == 1
    assert client.get("/v1/water").json()["items"][0]["completeness"] == 2
    wp = {w["item_id"]: w["value_usd"] for w in client.get("/v1/who-pays").json()["items"]}
    assert wp == {"WP-0001": 1900000000, "WP-0002": None}
    lr = client.get("/v1/load-reality").json()
    assert (lr["items"][0]["deliverability_score"], lr["items"][0]["score_weighted_mw"]) == (92, 966)
    assert [m["value"] for m in lr["market_context"]] == ["479", "331 / 195"]
    assert client.get("/v1/permit-checks").json()["items"][0]["gap"] == 58
    assert client.get("/v1/sources").json()["items"][0]["source"] == "Compute Atlas"
    with pool.connection() as conn:
        corr = conn.execute("SELECT to_jsonb(c)::text AS j FROM corrections c").fetchone()["j"]
        assert "person@example.org" not in corr

    # New IDs continue after the highest imported one, aliases included.
    fw = key_header(make_key(client, ["facilities:write"]))
    assert add_facility(client, fw)["facility_id"] == "DC-US-TX-0004"
    assert client.post("/v1/observations", json={"field_group": "Mitigation", "field": "x",
                                                 "source_url": "https://example.org/n"},
                       headers=key_header(make_key(client, ["observations:write"]))).json()["obs_id"] == "OBS-0005"


def test_reimport_changes_nothing(client, pool):
    _load_direct(pool)
    again = _load_direct(pool)
    assert again.loaded == {}, again.loaded
    assert client.get("/v1/facilities").json()["total"] == 5


def test_import_through_the_admin_endpoint(client):
    batches, report = importer.prepare(FIXTURES)
    for b in batches:
        r = client.post("/v1/admin/import", json={"table": b.table, "mode": b.mode, "rows": importer.jsonable(b.rows)},
                        headers=ADMIN)
        assert r.status_code == 200, r.text
        importer.record(report, b, r.json()["results"])
    assert report.loaded["facilities"] == 6 and report.loaded["observations"] == 3
    assert client.get("/v1/facilities/DC-US-OH-0001").json()["review_status"] == "Reviewed"


def test_cli_dry_run(tmp_path, capsys):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import import_sheet
    assert import_sheet.main([str(FIXTURES), "--dry-run", "--report", str(tmp_path / "r")]) == 0
    data = json.loads((tmp_path / "r.json").read_text())
    assert len(data["duplicate_ids"]) == 2
    assert "Ledger import report" in (tmp_path / "r.md").read_text()
