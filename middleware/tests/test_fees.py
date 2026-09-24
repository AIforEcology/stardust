import sqlite3
import uuid
from datetime import datetime, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from stardust_core.api import create_app
from stardust_core.config import DEFAULT_ESC, DEFAULT_METHODOLOGY, Settings
from stardust_core.fees import DEFAULT_FEE_PCT, FeePolicy, coverage
from stardust_core.store import _MIGRATIONS, SCHEMA_VERSION, Store

pytest.importorskip("stardust_provider")
from stardust_provider.examples.mock_reforestation import MockReforestationProvider  # noqa: E402
from stardust_provider.server import create_app as provider_app  # noqa: E402

ADMIN = {"X-Stardust-Admin-Token": "s3cret"}


# --- the policy ------------------------------------------------------------------------


def test_default_rate_and_rounding():
    assert DEFAULT_FEE_PCT == 8.0
    p = FeePolicy()
    assert p.fee_for(20.0) == 1.6            # $20 of removal → $1.60 fee
    assert p.fee_for(0.00002) == 0.000002    # pooled retail: fractions of a cent still work
    assert p.fee_for(0) == 0.0


def test_minimum_fee_and_zero_rate():
    assert FeePolicy(pct=5, min_usd=0.25).fee_for(1.0) == 0.25   # 5% would be $0.05
    assert FeePolicy(pct=5, min_usd=0.25).fee_for(100.0) == 5.0
    assert FeePolicy(pct=5, min_usd=0.25).fee_for(0) == 0.0      # nothing charged on a free quote
    assert FeePolicy(pct=0).fee_for(50.0) == 0.0


@pytest.mark.parametrize("pct,min_usd", [(-1, 0), (101, 0), (8, -0.01)])
def test_invalid_policies_are_rejected(pct, min_usd):
    with pytest.raises(ValueError):
        FeePolicy(pct=pct, min_usd=min_usd)


def test_coverage_prorates_the_monthly_budget():
    c = coverage(fees_usd=1000, monthly_operating_cost_usd=2000, days=365.25 / 12)
    assert c == {"operating_cost_usd": 2000.0, "coverage_pct": 50.0}
    half = coverage(fees_usd=1000, monthly_operating_cost_usd=2000, days=365.25 / 24)
    assert half["coverage_pct"] == 100.0


# --- through the API ------------------------------------------------------------------------


def core(pricing_file, db, **kw):
    settings = Settings(DEFAULT_METHODOLOGY, DEFAULT_ESC, pricing_file, "https://example.org/donate", "s3cret",
                        database_path=str(db), **kw)
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=provider_app(MockReforestationProvider())))
    c = TestClient(create_app(settings, http=http))
    c.__enter__()
    c.post("/v1/admin/providers", json={"base_url": "http://mock"}, headers=ADMIN)
    c.post("/v1/admin/providers/mock-reforestation/approve", json={"tier": "emerging"}, headers=ADMIN)
    return c


def test_quote_discloses_price_fee_and_total(pricing_file, tmp_path):
    c = core(pricing_file, tmp_path / "core.db")
    [q] = c.post("/v1/remediation/quotes", json={"co2e_g": 1_000_000}).json()["quotes"]  # 1 t at $20/t
    assert (q["price_usd"], q["fee_pct"], q["fee_usd"], q["total_usd"]) == (20.0, 8.0, 1.6, 21.6)
    terms = c.get("/v1/broker/terms").json()
    assert terms["fee_pct"] == 8.0 and terms["recipient"] == "AIforE"
    c.__exit__(None, None, None)


def test_order_keeps_the_fee_it_was_quoted_at(pricing_file, tmp_path):
    db = tmp_path / "core.db"
    c = core(pricing_file, db)
    [q] = c.post("/v1/remediation/quotes", json={"co2e_g": 1_000_000}).json()["quotes"]
    c.__exit__(None, None, None)

    # The rate changes to 12% before the order is placed: the quoted 8% still applies.
    c = core(pricing_file, db, broker_fee_pct=12.0)
    order = c.post("/v1/remediation/orders", json={"quote_id": q["quote_id"], "subscriber_id": "app"}).json()
    assert (order["provider_price_usd"], order["fee_pct"], order["fee_usd"], order["total_usd"]) == (20.0, 8.0, 1.6, 21.6)
    [new] = c.post("/v1/remediation/quotes", json={"co2e_g": 1_000_000}).json()["quotes"]
    assert (new["fee_pct"], new["fee_usd"], new["total_usd"]) == (12.0, 2.4, 22.4)
    c.__exit__(None, None, None)


def test_fee_report_shows_coverage(pricing_file, tmp_path):
    c = core(pricing_file, tmp_path / "core.db", operating_cost_monthly_usd=100.0)
    for co2e in (1_000_000, 2_000_000, 500_000):  # $20, $40, $10 of removal → $1.60 + $3.20 + $0.80 fees
        [q] = c.post("/v1/remediation/quotes", json={"co2e_g": co2e}).json()["quotes"]
        c.post("/v1/remediation/orders", json={"quote_id": q["quote_id"], "subscriber_id": "app"})

    assert c.get("/v1/admin/broker/fees").status_code == 401
    r = c.get("/v1/admin/broker/fees", headers=ADMIN, params={
        "since": datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat(),
        "until": datetime.now(timezone.utc).replace(hour=23, minute=59, second=59).isoformat(),
    }).json()
    assert r["orders"] == 3
    assert r["provider_payouts_usd"] == pytest.approx(70.0)
    assert r["fees_usd"] == pytest.approx(5.6)
    assert r["total_billed_usd"] == pytest.approx(75.6)
    assert r["failed_orders"] == 0
    # One day of a $100/month budget ≈ $3.29; $5.60 in fees covers ≈170% of it.
    assert r["operating_cost_usd"] == pytest.approx(100 / (365.25 / 12), abs=0.01)
    assert r["coverage_pct"] == pytest.approx(5.6 / (100 / (365.25 / 12)) * 100, abs=0.2)

    # Default window: the current calendar month.
    month = c.get("/v1/admin/broker/fees", headers=ADMIN).json()
    assert month["period"]["since"].endswith("-01T00:00:00+00:00")
    assert month["orders"] == 3
    c.__exit__(None, None, None)


def test_invalid_fee_setting_stops_core(pricing_file, tmp_path):
    settings = Settings(DEFAULT_METHODOLOGY, DEFAULT_ESC, pricing_file, "x", None,
                        database_path=str(tmp_path / "core.db"), broker_fee_pct=150.0)
    with pytest.raises(ValueError, match="between 0 and 100"):
        create_app(settings)


# --- migration ----------------------------------------------------------------------------------


def test_v1_database_migrates_to_v2_keeping_orders(tmp_path):
    db = tmp_path / "core.db"
    conn = sqlite3.connect(db)
    for statement in _MIGRATIONS[0].split(";"):
        if statement.strip():
            conn.execute(statement)
    conn.execute("PRAGMA user_version=1")
    oid = str(uuid.uuid4())
    conn.execute("INSERT INTO orders (order_id, subscriber_id, status, created_at, data) VALUES (?, 'app', 'fulfilled', ?, '{}')",
                 (oid, datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()

    store = Store(db)
    assert store.stats()["schema_version"] == SCHEMA_VERSION
    assert store.load_order(uuid.UUID(oid)) == {}  # old order still readable
    totals = store.fee_totals(datetime(2000, 1, 1, tzinfo=timezone.utc), datetime(2100, 1, 1, tzinfo=timezone.utc))
    assert totals["fulfilled"]["orders"] == 1 and totals["fulfilled"]["fees_usd"] == 0  # pre-fee order: no fee
