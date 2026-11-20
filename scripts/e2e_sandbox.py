#!/usr/bin/env python3
"""End-to-end check of the Python SDK against a Stardust Core, with a sandbox key.

    STARDUST_API_KEY=sdk_... python3 scripts/e2e_sandbox.py      # STARDUST_API_BASE defaults to hosted Core

1. Sends 3 metered events from recorded fake usage (no real LLM call).
2. Reads the summary and checks the totals went up.
3. Requests a quote, places an order, and polls its status until fulfilled.
4. Checks "sandbox": true on the quote, the order and the certificate.

Refuses to run with a live key, so it can never place a real order. Exits non-zero on any failure.
Uses the installed aifore-stardust, or the SDK in this repository if it isn't installed.
"""

from __future__ import annotations

import os
import sys
import time
import uuid
from pathlib import Path

try:
    from stardust_sdk import Stardust, StardustError
except ImportError:  # run from a clone without installing
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "subscriber-client" / "sdk-python" / "src"))
    from stardust_sdk import Stardust, StardustError

# Recorded usage, shaped like the vendors' own API responses (usage only; no prompt or reply text).
ANTHROPIC_MESSAGE = {"type": "message", "model": "claude-sonnet-4-5", "role": "assistant",
                     "usage": {"input_tokens": 1200, "output_tokens": 350, "cache_read_input_tokens": 0}}
OPENAI_COMPLETION = {"object": "chat.completion", "model": "gpt-4o-mini",
                     "usage": {"prompt_tokens": 800, "completion_tokens": 200, "total_tokens": 1000}}


def check(ok: bool, what: str) -> None:
    print(("  ok    " if ok else "  FAIL  ") + what)
    if not ok:
        raise SystemExit(1)


def main() -> int:
    if not os.environ.get("STARDUST_API_KEY"):
        print("Set STARDUST_API_KEY to a sandbox key (and STARDUST_API_BASE for a Core other than the hosted one).")
        return 2
    accepted = []
    sd = Stardust(region="us-east-1", on_result=accepted.append)
    print(f"Stardust Core: {sd.api_base} (Python SDK)")

    before = sd.summary()
    if not before.sandbox:
        print("This is a live key. The end-to-end check places an order, so it only runs with a sandbox key.")
        return 2
    check(True, f"sandbox key; summary before: {before.events} events, {before.co2e_g:.6f} g CO2e")

    run = uuid.uuid4().hex[:8]
    sd.record_response(ANTHROPIC_MESSAGE)
    sd.record_response(OPENAI_COMPLETION)
    sd.record("google", "gemini-2.5-flash", 400, 120, operation="chat")
    check(sd.flush(30), f"3 events sent (run {run})")
    check(len(accepted) == 3 and sd.dropped == 0, f"Core accepted all 3 ({len(accepted)} accepted, {sd.dropped} dropped)")
    check(all(e.get("sandbox") is True for e in accepted), "each enriched event says sandbox")

    after = sd.summary()
    check(after.events >= before.events + 3, f"summary events went up: {before.events} -> {after.events}")
    check(after.co2e_g > before.co2e_g, f"summary CO2e went up: {before.co2e_g:.6f} -> {after.co2e_g:.6f} g")
    check(after.energy_wh > before.energy_wh and after.water_ml > before.water_ml, "energy and water went up")
    check(bool(after.indicator_code), f"indicator code: {after.indicator_code}")
    check(len(sd.summary_daily()) >= 1, "daily summary has at least one day")

    co2e_g = max(after.co2e_g - before.co2e_g, 1.0)
    quotes = sd.request_quote(co2e_g)
    check(len(quotes) >= 1, f"{len(quotes)} quote(s) for {co2e_g:.4f} g")
    check(all(q.sandbox for q in quotes), "every quote is sandbox")
    quote = quotes[0]
    print(f"        {quote.provider_id}: ${quote.price_usd} + fee ${quote.fee_usd} = ${quote.total_usd}")

    order = sd.submit_order(quote, payment_ref=f"e2e-sandbox-{run}")
    check(order.sandbox, f"order {order.remediation_order_id} is sandbox")
    check(bool(order.notice), f"order notice: {order.notice}")

    deadline = time.monotonic() + 60
    status = sd.order_status(order)
    while status.fulfillment_status in ("ordered", "fulfilling") and time.monotonic() < deadline:
        time.sleep(2)
        status = sd.order_status(order)
    check(status.fulfillment_status == "fulfilled", f"order status: {status.fulfillment_status}")
    check(status.sandbox, "order status is sandbox")
    check(status.certificate is not None and status.certificate.sandbox,
          f"certificate is sandbox: {status.certificate.ref if status.certificate else None}")
    sd.close(5)
    print("Passed.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except StardustError as e:
        print(f"  FAIL  {e}")
        sys.exit(1)
