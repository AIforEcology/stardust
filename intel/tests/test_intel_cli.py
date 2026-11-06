"""The stardust-intel commands."""

import json

import pytest

from dci_testdata import core_cfg, ledger, recorded  # noqa: F401 (core_cfg, ledger are fixtures)
from stardust_intel import cli


@pytest.fixture
def fake_ledger(monkeypatch, ledger):
    monkeypatch.setattr(cli, "ApiLedger", lambda url: ledger)
    monkeypatch.delenv("LEDGER_DATABASE_URL", raising=False)


def test_validate_committed_data(capsys):
    assert cli.main(["validate"]) == 0
    assert "ok:" in capsys.readouterr().out


def test_validate_reports_problems(tmp_path, capsys):
    bad = tmp_path / "allocations.json"
    data = json.loads(cli.DEFAULT_ALLOCATIONS.read_text())
    data["providers"]["openai"]["components"][0]["weight"] = 0.9
    bad.write_text(json.dumps(data))
    assert cli.main(["validate", "--allocations", str(bad)]) == 1
    assert "weights add up to 1.7" in capsys.readouterr().err


def test_footprint_then_check(tmp_path, fake_ledger, capsys):
    out = tmp_path / "footprint.json"
    assert cli.main(["footprint", "-o", str(out)]) == 0
    assert json.loads(out.read_text())["provider_default_grid"]["anthropic"] == "fp:anthropic"
    assert "anthropic:" in capsys.readouterr().err
    assert cli.main(["footprint", "-o", str(out), "--check"]) == 0

    stale = json.loads(out.read_text())
    stale["providers"]["anthropic"]["intensity_g_per_kwh"] = 1.0
    out.write_text(json.dumps(stale))
    assert cli.main(["footprint", "-o", str(out), "--check"]) == 1
    assert "stale" in capsys.readouterr().err


def test_eia_state_needs_key(monkeypatch, capsys):
    monkeypatch.delenv("EIA_API_KEY", raising=False)
    assert cli.main(["eia-state", "IN", "--year", "2025"]) == 2
    assert "EIA_API_KEY" in capsys.readouterr().err


def test_eia_state_writes_grids(monkeypatch, tmp_path):
    monkeypatch.setenv("EIA_API_KEY", "k")

    def fake_fetch(state, year, key):
        from stardust_intel.eia import build_state_grid
        import datetime as dt
        return build_state_grid(state, year, recorded("generation-TN-2025.json"),
                                recorded("co2-rate-TN-2024-2025.json"), dt.date(2026, 11, 5))

    monkeypatch.setattr(cli, "fetch_state_grid", fake_fetch)
    grids = tmp_path / "grids.json"
    assert cli.main(["eia-state", "TN", "--year", "2025", "--grids", str(grids)]) == 0
    assert json.loads(grids.read_text())["grids"]["US-TN"]["intensity_g_per_kwh"] == pytest.approx(371.0)
