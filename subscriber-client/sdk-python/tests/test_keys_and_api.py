"""API keys (Authorization: Bearer) and the reading/remediation helpers."""

import json
import logging
from datetime import datetime, timezone

import pytest

from stardust_sdk import Order, Quote, Stardust, StardustError, Summary
from stardust_sdk.client import AUTH_WARNING, DEFAULT_API_BASE


KEY = "sdk_0123456789abcdef_s3cr3t-value-that-must-never-leak"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("STARDUST_API_KEY", raising=False)
    monkeypatch.delenv("STARDUST_API_BASE", raising=False)


def test_key_is_sent_as_bearer_header(make_core):
    core = make_core()
    sd = Stardust("http://core.test", api_key=KEY, transport=core)
    sd.record("anthropic", "claude-sonnet-5", 10, 5)
    assert sd.flush(2)
    assert core.headers[0]["authorization"] == f"Bearer {KEY}"
    sd.close(1)


def test_no_key_sends_no_header(make_core):
    core = make_core()
    sd = Stardust("http://core.test", transport=core)
    sd.record("anthropic", "claude-sonnet-5", 10, 5)
    assert sd.flush(2)
    assert "authorization" not in core.headers[0]
    sd.close(1)


def test_key_and_base_from_environment(monkeypatch, make_core):
    monkeypatch.setenv("STARDUST_API_KEY", KEY)
    core = make_core()
    sd = Stardust("http://core.test", transport=core)
    sd.record("anthropic", "claude-sonnet-5", 10, 5)
    assert sd.flush(2)
    assert core.headers[0]["authorization"] == f"Bearer {KEY}"
    sd.close(1)


def test_api_base_defaults_to_hosted_core_and_env_overrides(monkeypatch, make_core):
    sd = Stardust(transport=make_core())
    assert sd.url == DEFAULT_API_BASE + "/v1/events"
    sd.close(0)
    monkeypatch.setenv("STARDUST_API_BASE", "http://override.test/")
    sd = Stardust(transport=make_core())
    assert sd.url == "http://override.test/v1/events"
    sd.close(0)


@pytest.mark.parametrize("status", [401, 403])
def test_auth_failure_warns_once_and_does_not_retry(status, caplog, make_core):
    core = make_core([status, status, status])
    sd = Stardust("http://core.test", api_key=KEY, transport=core)
    with caplog.at_level(logging.DEBUG, logger="stardust_sdk"):
        for _ in range(3):
            sd.record("anthropic", "claude-sonnet-5", 10, 5)  # never raises
        assert sd.flush(2)
    assert core.calls == 3  # one attempt per event, no retries
    assert core.events == []
    assert sd.dropped == 3
    assert [r.getMessage() for r in caplog.records].count(AUTH_WARNING) == 1
    sd.close(1)


def test_network_errors_and_5xx_still_retry(make_core):
    core = make_core([OSError("down"), 503, 200])
    sd = Stardust("http://core.test", api_key=KEY, transport=core)
    sd.record("anthropic", "claude-sonnet-5", 10, 5)
    assert sd.flush(5)
    assert core.calls == 3 and len(core.events) == 1
    sd.close(1)


def test_key_never_appears_in_logs_repr_or_errors(caplog, make_core):
    core = make_core([401, 422, OSError("down"), 200])
    sd = Stardust("http://core.test", api_key=KEY, transport=core)
    with caplog.at_level(logging.DEBUG, logger="stardust_sdk"):
        for _ in range(3):
            sd.record("anthropic", "claude-sonnet-5", 10, 5)
        sd.flush(5)
    assert KEY not in caplog.text
    assert KEY not in repr(sd) and "api_key=set" in repr(sd)
    assert KEY not in str(vars(sd))

    def unauthorized(*_):
        return 401, b'{"detail": "invalid API key", "reason": "unauthorized"}'

    sd2 = Stardust("http://core.test", api_key=KEY, transport=unauthorized)
    with pytest.raises(StardustError) as e:
        sd2.summary()
    assert e.value.status == 401 and e.value.reason == "unauthorized"
    assert KEY not in str(e.value) and KEY not in repr(e.value)
    sd.close(1)
    sd2.close(0)


# --- helpers ------------------------------------------------------------------------------------


class FakeApi:
    """Answers the reading and remediation endpoints; records every request."""

    def __init__(self, responses):
        self.responses = responses  # {(method, path): (status, body)}
        self.requests = []

    def __call__(self, method, url, body, timeout, headers):
        path, _, query = url.partition("?")
        path = path.replace("http://core.test", "")
        self.requests.append({"method": method, "path": path, "query": query,
                              "body": json.loads(body) if body else None, "headers": headers})
        status, payload = self.responses[(method, path)]
        return status, json.dumps(payload).encode()


SUMMARY = {"events": 3, "tokens_in": 300, "tokens_out": 60, "tokens_cached_in": 0, "cost_usd": 0.01,
           "cost_unknown_events": 0, "energy_wh": 1.5, "co2e_g": 0.5, "water_ml": 2.0, "indicator_code": "B2-S",
           "methodology_version": "0.2.0", "sandbox": True, "heat_rejected_wh": 1.5}
QUOTE = {"quote_id": "11111111-1111-4111-8111-111111111111", "provider_id": "mock-reforestation",
         "project_id": "mock-project-1", "co2e_g": 1000.0, "price_usd": 0.02, "fee_pct": 8.0, "fee_usd": 0.0016,
         "total_usd": 0.0216, "fulfillment_days": 0, "provider_tier": "emerging",
         "expires_at": "2026-11-19T12:15:00+00:00", "sandbox": True}
ORDER = {"remediation_order_id": "22222222-2222-4222-8222-222222222222", "quote_id": QUOTE["quote_id"],
         "provider_id": "mock-reforestation", "provider_tier": "emerging", "co2e_g_offset": 1000.0,
         "fulfillment_status": "fulfilled", "certificate_ref": "SANDBOX-not-a-real-removal-2222",
         "certificate": {"ref": "SANDBOX-not-a-real-removal-2222", "sandbox": True,
                         "note": "SANDBOX — not a real removal"},
         "total_usd": 0.0216, "fee_usd": 0.0016, "sandbox": True,
         "notice": "Sandbox test order: no carbon was removed and no money moved."}


def api(responses):
    fake = FakeApi(responses)
    return Stardust("http://core.test", api_key=KEY, transport=fake), fake


def test_summary_and_daily():
    sd, fake = api({("GET", "/v1/summary"): (200, SUMMARY),
                    ("GET", "/v1/summary/daily"): (200, {"days": [{"day": "2026-11-19", "events": 3, "tokens_in": 300,
                                                                   "tokens_out": 60, "cost_usd": 0.01, "energy_wh": 1.5,
                                                                   "co2e_g": 0.5, "water_ml": 2.0}]})})
    s = sd.summary(since=datetime(2026, 11, 1, tzinfo=timezone.utc))
    assert isinstance(s, Summary) and s.co2e_g == 0.5 and s.indicator_code == "B2-S" and s.sandbox
    assert s.raw["heat_rejected_wh"] == 1.5  # fields the SDK doesn't type yet are kept
    assert fake.requests[0]["query"] == "since=2026-11-01T00%3A00%3A00%2B00%3A00"
    assert fake.requests[0]["headers"]["authorization"] == f"Bearer {KEY}"
    days = sd.summary_daily()
    assert days[0].day == "2026-11-19" and days[0].events == 3
    assert fake.requests[1]["query"] == ""
    sd.close(0)


def test_quote_order_and_status():
    sd, fake = api({("POST", "/v1/remediation/quotes"): (200, {"quotes": [QUOTE]}),
                    ("POST", "/v1/remediation/orders"): (200, ORDER),
                    ("GET", f"/v1/remediation/orders/{ORDER['remediation_order_id']}"): (200, ORDER)})
    [q] = sd.request_quote(1000, tier_preference=["emerging"])
    assert isinstance(q, Quote) and q.sandbox and q.total_usd == 0.0216
    assert fake.requests[0]["body"] == {"co2e_g": 1000, "tier_preference": ["emerging"]}

    order = sd.submit_order(q, payment_ref="test-ref")
    assert isinstance(order, Order) and order.fulfilled and order.sandbox
    assert order.certificate.sandbox and order.notice.startswith("Sandbox test order")
    assert fake.requests[1]["body"] == {"quote_id": QUOTE["quote_id"], "payment_ref": "test-ref"}

    status = sd.order_status(order)
    assert status.remediation_order_id == ORDER["remediation_order_id"]
    assert fake.requests[2]["method"] == "GET"
    sd.close(0)


def test_helper_errors_raise():
    sd, _ = api({("POST", "/v1/remediation/orders"): (400, {"detail": "unknown or already-used quote"}),
                 ("GET", "/v1/remediation/orders/x"): (404, {"detail": "unknown order"})})
    with pytest.raises(StardustError, match="already-used") as e:
        sd.submit_order("q")
    assert e.value.status == 400
    with pytest.raises(StardustError) as e:
        sd.order_status("x")
    assert e.value.status == 404
    sd.close(0)

    def down(*_):
        raise OSError("connection refused")

    sd = Stardust("http://core.test", transport=down)
    with pytest.raises(StardustError) as e:
        sd.summary()
    assert e.value.status == 0
    sd.close(0)


def test_helpers_need_api_base():
    from opentelemetry.sdk.trace import TracerProvider

    sd = Stardust(None, otel=TracerProvider())
    with pytest.raises(StardustError, match="no api_base"):
        sd.summary()
