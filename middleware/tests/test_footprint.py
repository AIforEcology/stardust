"""The DCI footprint overlay (STARDUST_FOOTPRINT_FILE, footprint.py)."""

import copy
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from stardust_core.api import create_app
from stardust_core.config import DEFAULT_ESC, DEFAULT_METHODOLOGY, Settings, load_json
from stardust_core.footprint import FootprintError, apply_footprint

ESC = load_json(DEFAULT_ESC)["codes"].keys()

FOOTPRINT = {
    "format": "stardust-footprint",
    "dci_version": "0.1.0",
    "as_of": "2026-11-05",
    "status": "unpublished",
    "core_methodology_version": "0.2.0",
    "ledger": {"source": "https://core.aifore.org/ledger", "facilities": 3},
    "grids": {
        "fp:anthropic": {"intensity_g_per_kwh": 456.0, "mix": {"FPP": 26, "NGP": 38, "NPP": 14, "WPP": 9, "HYD": 5,
                                                               "SPP": 5, "UNK": 3},
                         "confidence": "estimated", "citation": "Stardust DCI 0.1.0 footprint for anthropic"},
    },
    "provider_default_grid": {"anthropic": "fp:anthropic"},
    "providers": {"anthropic": {"intensity_g_per_kwh": 456.0, "caveats": ["43% of compute at undisclosed sites"]}},
}

EVENT = {"source_layer": "browser_ext", "provider": "anthropic", "model": "claude-opus-5-5",
         "timestamp": "2026-10-21T12:00:00Z", "tokens_in": 2000, "tokens_out": 500}


def write(tmp_path: Path, fp: dict) -> Path:
    p = tmp_path / "footprint.json"
    p.write_text(json.dumps(fp))
    return p


def settings(pricing_file: Path, footprint: Path) -> Settings:
    return Settings(DEFAULT_METHODOLOGY, DEFAULT_ESC, pricing_file, "https://example.org/donate", None,
                    footprint_path=footprint)


@pytest.fixture
def client(pricing_file, tmp_path):
    with TestClient(create_app(settings(pricing_file, write(tmp_path, FOOTPRINT)))) as c:
        yield c


def test_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("STARDUST_FOOTPRINT_FILE", str(tmp_path / "fp.json"))
    assert Settings.from_env().footprint_path == tmp_path / "fp.json"


def test_provider_without_region_uses_footprint_and_is_estimated(client):
    e = client.post("/v1/events", json=EVENT).json()
    # The spec's worked example: Claude Opus 5.5, 2,000 in / 500 out = 0.8 Wh; × 456 g/kWh = 0.365 g.
    assert e["grid_intensity_g_per_kwh"] == 456.0
    assert e["co2e_g"] == pytest.approx(0.3648)
    assert e["energy_mix_breakdown"]["FPP"] == 26
    assert e["confidence_tier"] == "estimated"
    assert e["methodology_version"] == "0.2.0+dci-0.1.0"


def test_region_still_wins(client):
    e = client.post("/v1/events", json={**EVENT, "region": "us-east-1"}).json()
    assert e["grid_intensity_g_per_kwh"] == 367.0
    assert e["confidence_tier"] == "modeled"
    # The version suffix is on every event while a footprint is loaded.
    assert e["methodology_version"] == "0.2.0+dci-0.1.0"


def test_other_providers_unchanged(client):
    e = client.post("/v1/events", json={**EVENT, "provider": "openai", "model": "gpt-5"}).json()
    assert e["grid_intensity_g_per_kwh"] == 367.0
    assert e["confidence_tier"] == "modeled"


def test_health_and_methodology_show_footprint(client):
    h = client.get("/healthz").json()
    assert h["methodology_version"] == "0.2.0+dci-0.1.0"
    assert h["footprint"]["dci_version"] == "0.1.0"
    assert h["footprint"]["providers"] == {"anthropic": "fp:anthropic"}
    m = client.get("/v1/methodology").json()
    assert m["grids"]["fp:anthropic"]["estimated"] is True
    assert m["provider_default_grid"]["anthropic"] == "fp:anthropic"
    assert m["footprint"]["providers"]["anthropic"]["caveats"]
    # Core's own grids are untouched.
    assert m["grids"]["US"] == load_json(DEFAULT_METHODOLOGY)["grids"]["US"]


def test_does_not_modify_cached_methodology(cfg):
    before = copy.deepcopy(cfg)
    apply_footprint(cfg, FOOTPRINT, ESC)
    assert cfg == before


def test_may_point_provider_at_existing_core_grid(cfg):
    fp = copy.deepcopy(FOOTPRINT)
    fp["provider_default_grid"]["google"] = "US"
    assert apply_footprint(cfg, fp, ESC)["provider_default_grid"]["google"] == "US"


def broken(**changes):
    fp = copy.deepcopy(FOOTPRINT)
    for path, value in changes.items():
        *parents, leaf = path.split("__")
        node = fp
        for p in parents:
            node = node[p]
        node[leaf] = value
    return fp


@pytest.mark.parametrize("fp,message", [
    (broken(provider_default_grid={"anthropic": "fp:missing"}), "which Core doesn't define"),
    (broken(provider_default_grid={"openai": "EU"}), "which Core doesn't define"),
    (broken(provider_default_grid={"default": "fp:anthropic"}), "default grid"),
    (broken(provider_default_grid={"Anthropic": "fp:anthropic"}), "lower case"),
    (broken(format="something-else"), "not a footprint file"),
    (broken(dci_version="latest"), "dci_version"),
    (broken(core_methodology_version="0.1.0"), "rebuild it"),
    (broken(grids={"US": FOOTPRINT["grids"]["fp:anthropic"]}), "must be named fp:"),
    (broken(grids={}), "no grids"),
])
def test_refuses_bad_footprint(cfg, fp, message):
    with pytest.raises(FootprintError, match=message):
        apply_footprint(cfg, fp, ESC)


def grid(**kw):
    g = dict(FOOTPRINT["grids"]["fp:anthropic"], **kw)
    return {**FOOTPRINT, "grids": {"fp:anthropic": g}}


@pytest.mark.parametrize("fp,message", [
    (grid(intensity_g_per_kwh=-1), "intensity_g_per_kwh"),
    (grid(intensity_g_per_kwh="456"), "intensity_g_per_kwh"),
    (grid(confidence="guessed"), "confidence"),
    (grid(mix={"COAL": 100}), "unknown energy source codes"),
    (grid(mix={"FPP": 60, "NGP": 20}), "not 100%"),
])
def test_refuses_bad_grid(cfg, fp, message):
    with pytest.raises(FootprintError, match=message):
        apply_footprint(cfg, fp, ESC)


def test_core_refuses_to_start(pricing_file, tmp_path):
    bad = write(tmp_path, broken(provider_default_grid={"anthropic": "fp:nowhere"}))
    with pytest.raises(FootprintError):
        create_app(settings(pricing_file, bad))
    with pytest.raises(FootprintError, match="cannot read"):
        create_app(settings(pricing_file, tmp_path / "missing.json"))
