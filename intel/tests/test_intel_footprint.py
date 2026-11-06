"""Footprint building from the (synthetic) ledger, the committed grids and allocations, and Core's grid."""

import copy
import datetime as dt
import json

import pytest

from dci_testdata import core_cfg, ledger, INTEL  # noqa: F401 (core_cfg, ledger are fixtures)
from stardust_intel.allocations import load_allocations, validate_allocations
from stardust_intel.footprint import build_footprint, comparable
from stardust_intel.grids import load_grids

ALLOCATIONS = load_allocations(INTEL / "data" / "allocations.json")
GRIDS = load_grids(INTEL / "data" / "grids.json")
TODAY = dt.date(2026, 11, 5)


@pytest.fixture
def fp(core_cfg, ledger):
    return build_footprint(ALLOCATIONS, GRIDS, core_cfg, ledger, today=TODAY)


def test_committed_allocations_are_valid():
    assert validate_allocations(ALLOCATIONS) == []


def test_anthropic_blend(fp, core_cfg):
    a = fp["providers"]["anthropic"]
    us = float(core_cfg["grids"]["US"]["intensity_g_per_kwh"])
    ind, tn = GRIDS["grids"]["US-IN"]["intensity_g_per_kwh"], GRIDS["grids"]["US-TN"]["intensity_g_per_kwh"]
    # Rainier IN 30% on Indiana, Colossus 1 15% on Tennessee. Rainier MS and PA split 10% equally (PA isn't in
    # the ledger; MS has no grid record), Lake Mariner 2% (no NY grid record) and 43% undisclosed: all on US.
    expected = 0.30 * ind + 0.15 * tn + (0.05 + 0.05 + 0.02 + 0.43) * us
    assert a["intensity_g_per_kwh"] == pytest.approx(expected, abs=0.05)
    assert a["confidence"] == "estimated"
    assert a["known_site_share"] == pytest.approx(0.30 + 0.15 + 0.05 + 0.02)
    assert sum(a["mix"].values()) == pytest.approx(100, abs=1)
    assert a["vs_flat_pct"] > 0  # Indiana's coal outweighs Tennessee's nuclear


def test_caveats_name_every_gap(fp):
    caveats = "\n".join(fp["providers"]["anthropic"]["caveats"])
    assert "43% of anthropic's compute is at undisclosed sites" in caveats
    assert "'Rainier' in US-PA matched 0 data centers" in caveats          # not in the ledger
    assert "no grid record for US-MS" in caveats                         # in the ledger, no EIA record yet
    assert "on-site generation reported (Gas turbines: 15 turbines" in caveats
    assert "DC-US-VA-0001" in caveats                                     # Anthropic tenant with no weight
    # Lake Mariner's tenant field lists Anthropic among others: no tenant caveat.
    assert "does not list anthropic" not in caveats
    assert any("pin facility_id" in n for n in fp["providers"]["anthropic"]["notes"])


def test_power_plants_and_onsite_are_carried(fp):
    colossus = next(c for c in fp["providers"]["anthropic"]["components"] if c["id"] == "colossus-1")
    f = colossus["facilities"][0]
    assert f["facility_id"] == "DC-US-TN-0001"
    assert f["grid"] == "US-TN"
    assert f["power_plants"][0]["facility_id"] == "PWR-US-TN-0001"
    assert f["onsite_power"][0]["field"] == "Gas turbines"


def test_openai(fp, core_cfg):
    o = fp["providers"]["openai"]
    # Abilene is in the ledger, but there is no Texas grid record yet: everything on the US grid.
    assert o["intensity_g_per_kwh"] == pytest.approx(float(core_cfg["grids"]["US"]["intensity_g_per_kwh"]))
    assert o["known_site_share"] == pytest.approx(0.20)
    assert any("no grid record for US-TX" in c for c in o["caveats"])


def test_file_shape(fp):
    assert fp["format"] == "stardust-footprint"
    assert fp["provider_default_grid"] == {"anthropic": "fp:anthropic", "openai": "fp:openai"}
    assert set(fp["grids"]) == {"fp:anthropic", "fp:openai"}
    assert all(g["confidence"] == "estimated" for g in fp["grids"].values())
    assert fp["status"].startswith("unpublished")
    json.dumps(fp)  # serialisable


def test_mw_split(core_cfg, ledger):
    a = copy.deepcopy(ALLOCATIONS)
    a["providers"]["anthropic"]["components"][0]["facilities"] = [{"facility_id": "DC-US-IN-0001"},
                                                                  {"facility_id": "DC-US-TN-0001"}]
    c = build_footprint(a, GRIDS, core_cfg, ledger, today=TODAY)["providers"]["anthropic"]["components"][0]
    assert c["split"] == "operational_mw"
    assert [f["share"] for f in c["facilities"]] == pytest.approx([0.30 * 2200 / 2500, 0.30 * 300 / 2500])


def test_comparable_ignores_volatile(fp):
    later = dict(fp, generated_on="2027-01-01", ledger={"source": "x", "facilities": 99})
    assert comparable(later) == comparable(fp)
    assert comparable(dict(fp, as_of="2027-01-01")) != comparable(fp)


def test_core_loads_the_footprint(fp, core_cfg):
    core = pytest.importorskip("stardust_core.footprint")
    esc = json.loads((INTEL.parent / "schema" / "esc.json").read_text())["codes"].keys()
    merged = core.apply_footprint(core_cfg, fp, esc)
    assert merged["version"] == "0.2.0+dci-0.1.0"
    assert merged["grids"]["fp:anthropic"]["estimated"] is True
    assert merged["provider_default_grid"]["anthropic"] == "fp:anthropic"


@pytest.mark.parametrize("change,problem", [
    (lambda a: a["providers"]["anthropic"]["components"][0].update(weight=0.5), "not 1"),
    (lambda a: a["providers"]["openai"]["components"][0].pop("rationale"), "rationale"),
    (lambda a: a["providers"]["openai"]["components"][0].update(sources=[]), "source URL"),
    (lambda a: a["providers"]["openai"]["components"][0].update(facilities=[{"facility_id": "rainier"}]),
     "bad facility_id"),
    (lambda a: a["providers"]["openai"]["components"][0].update(facilities=[{"match": {"q": "x", "state": "TX"}}]),
     "ISO 3166-2"),
    (lambda a: a["providers"]["openai"]["components"][1].update(facilities=[{"facility_id": "DC-US-TX-0001"}]),
     "undisclosed component"),
    (lambda a: a.update(fallback_grid="US"), "fallback_grid"),
])
def test_invalid_allocations(change, problem):
    a = copy.deepcopy(ALLOCATIONS)
    change(a)
    assert any(problem in p for p in validate_allocations(a)), validate_allocations(a)
