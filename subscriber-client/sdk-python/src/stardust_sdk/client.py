"""Stardust client: queues usage events and sends them to Stardust Core in the background.

Recording never blocks or raises in the caller's code path (spec §15: no perceptible
latency; never a blocked user action). Events are buffered in memory and retried with
backoff while Core is unreachable (§15 offline resilience).
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import threading
import urllib.error
import urllib.request
import uuid
from collections import deque
from datetime import datetime, timezone
from datetime import date
from typing import Any, Callable, Deque, Dict, List, Optional, Sequence, Tuple, Union
from urllib.parse import quote as _quote, urlencode

from .extract import Usage, extract
from .otel import DEFAULT_OPERATION, Call, OtelEmitter, event_id_for_span
from .results import DailySummary, Order, Quote, StardustError, Summary

log = logging.getLogger("stardust_sdk")

DEFAULT_API_BASE = "https://core.aifore.org"
AUTH_WARNING = "Stardust: API key missing or invalid; events are being dropped"

# (method, url, body or None, timeout, headers) -> (status, response body)
Transport = Callable[[str, str, Optional[bytes], float, Dict[str, str]], Tuple[int, bytes]]


def _urllib_transport(method: str, url: str, body: Optional[bytes], timeout: float,
                      headers: Dict[str, str]) -> Tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


class _Unset:
    def __repr__(self) -> str:
        return "<default>"


_UNSET: Any = _Unset()


class _Secret:
    """Holds the API key so it never shows up in repr(), vars() or a debugger's object dump."""

    __slots__ = ("_value",)

    def __init__(self, value: str):
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "<redacted>"

    __str__ = __repr__
TimeArg = Union[datetime, date, str, None]


def _iso(value: TimeArg) -> Optional[str]:
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, datetime) and value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


class Stardust:
    """Report AI usage to Stardust Core.

    >>> stardust = Stardust(region="us-east-1")             # hosted Core, key from STARDUST_API_KEY
    >>> stardust.record_response(anthropic_message)       # or instrument(client, stardust)

    ``api_base`` defaults to ``STARDUST_API_BASE``, else https://core.aifore.org. ``api_key``
    defaults to ``STARDUST_API_KEY`` and is sent as ``Authorization: Bearer <key>``.

    ``otel=True`` (or a TracerProvider) also emits an OpenTelemetry GenAI span per call.
    ``api_base=None`` with ``otel=True`` sends nothing directly: spans travel through
    your own collector to Core's OTLP receiver.
    """

    MAX_BACKOFF_S = 60.0
    # While flush() is waiting, retry quickly instead of backing off.
    FLUSH_RETRY_S = 0.05

    def __init__(
        self,
        api_base: Optional[str] = _UNSET,
        *,
        api_key: Optional[str] = None,
        user_id: Optional[str] = None,
        org_id: Optional[str] = None,
        region: Optional[str] = None,
        enabled: bool = True,
        max_buffer: int = 10_000,
        timeout: float = 5.0,
        on_result: Optional[Callable[[Dict[str, Any]], None]] = None,
        transport: Optional[Transport] = None,
        otel: Any = False,
    ):
        if api_base is _UNSET:
            api_base = os.environ.get("STARDUST_API_BASE") or DEFAULT_API_BASE
        if api_base is None and not otel:
            raise ValueError("Stardust needs api_base, otel, or both")
        self.api_base: Optional[str] = api_base.rstrip("/") if api_base else None
        self.url = self.api_base + "/v1/events" if self.api_base else None
        # Kept private and out of repr, logs and exceptions.
        key = api_key or os.environ.get("STARDUST_API_KEY") or None
        self.__api_key: Optional[_Secret] = _Secret(key) if key else None
        self._auth_warned = False
        self._otel: Optional[OtelEmitter] = None
        if otel:
            self._otel = OtelEmitter(None if otel is True else otel)
        self.user_id, self.org_id, self.region = user_id, org_id, region
        self.enabled = enabled
        self.timeout = timeout
        self.on_result = on_result
        self._transport = transport or _urllib_transport
        self._buffer: Deque[Dict[str, Any]] = deque(maxlen=max_buffer)
        self._cond = threading.Condition()
        self._in_flight = 0
        self._closed = False
        self._backoff = 0.0
        self._flushing = 0
        self.dropped = 0
        if self.url:
            self._worker = threading.Thread(target=self._run, name="stardust-sdk", daemon=True)
            self._worker.start()
            atexit.register(self.close, 2.0)

    def __repr__(self) -> str:
        return (f"Stardust(api_base={self.api_base!r}, api_key={'set' if self.__api_key else 'unset'}, "
                f"region={self.region!r}, enabled={self.enabled!r})")

    @property
    def has_api_key(self) -> bool:
        return self.__api_key is not None

    def _headers(self, json_body: bool) -> Dict[str, str]:
        h = {"accept": "application/json"}
        if json_body:
            h["content-type"] = "application/json"
        if self.__api_key:
            h["authorization"] = f"Bearer {self.__api_key.reveal()}"
        return h

    # --- recording -----------------------------------------------------------

    def record(
        self,
        provider: str,
        model: str,
        tokens_in: Optional[int],
        tokens_out: Optional[int],
        *,
        tokens_cached_in: Optional[int] = None,
        region: Optional[str] = None,
        operation: Optional[str] = None,
    ) -> None:
        """Record one operation's usage. With OpenTelemetry on, this also emits a (zero-length) span."""
        self.record_usage(Usage(provider, model, tokens_in, tokens_out, tokens_cached_in),
                          region=region, operation=operation)

    def record_usage(self, usage: Usage, *, region: Optional[str] = None, operation: Optional[str] = None) -> None:
        if not self.enabled or self._closed:
            return
        op = operation or DEFAULT_OPERATION.get(usage.provider, "chat")
        self.start_call(usage.provider, op, usage.model, region).finish(usage)

    def record_response(self, response: Any, *, provider: Optional[str] = None, model: Optional[str] = None,
                        region: Optional[str] = None) -> Optional[Usage]:
        """Record an Anthropic, OpenAI or Gemini response. Returns the usage found, if any. Never raises."""
        try:
            usage = extract(response, provider, model)
            if usage is not None:
                self.record_usage(usage, region=region)
            return usage
        except Exception:  # noqa: BLE001 - metering must never break the caller
            log.exception("Stardust could not read usage from a response")
            return None

    def start_call(self, provider: str, operation: str, model: Optional[str], region: Optional[str] = None) -> Call:
        """Begin metering a call; used by instrument(). ``Call.finish(usage)`` completes it."""
        return Call(self, provider, operation, model, region or self.region)

    # --- internals used by Call ------------------------------------------------------

    def _span_attributes(self, region: Optional[str]) -> Dict[str, Any]:
        return {
            "cloud.region": region,
            "stardust.source_layer": "infra_agent",
            "stardust.user_id": self.user_id,
            "stardust.org_id": self.org_id,
        }

    @staticmethod
    def _log_exception(message: str) -> None:
        log.exception(message)

    def _queue_usage(self, usage: Usage, *, region: Optional[str], trace_ids: Optional[Tuple[str, str]]) -> None:
        """Queue a usage event for Core, if direct sending is on."""
        if not self.url or not self.enabled or self._closed:
            return
        event: Dict[str, Any] = {
            "event_id": str(event_id_for_span(*trace_ids) if trace_ids else uuid.uuid4()),
            "source_layer": "infra_agent",
            "provider": usage.provider,
            "model": usage.model,
            "timestamp": datetime.now(timezone.utc).astimezone().isoformat(),
            "tokens_in": usage.tokens_in,
            "tokens_out": usage.tokens_out,
            "tokens_estimated": False,
        }
        optional = {
            "tokens_cached_in": usage.tokens_cached_in,
            "region": region or self.region,
            "user_id": self.user_id,
            "org_id": self.org_id,
            "otel_trace_id": trace_ids[0] if trace_ids else None,
            "otel_span_id": trace_ids[1] if trace_ids else None,
        }
        event.update({k: v for k, v in optional.items() if v is not None})
        with self._cond:
            if len(self._buffer) == self._buffer.maxlen:
                self.dropped += 1  # deque drops the oldest
                log.warning("Stardust buffer full; dropping the oldest event")
            self._buffer.append(event)
            self._cond.notify()

    # --- lifecycle -------------------------------------------------------------

    @property
    def pending(self) -> int:
        with self._cond:
            return len(self._buffer) + self._in_flight

    def flush(self, timeout: Optional[float] = None) -> bool:
        """Wait until every queued event is sent. Returns False on timeout."""
        with self._cond:
            self._flushing += 1
            self._cond.notify_all()  # cut short any backoff in progress
            try:
                return self._cond.wait_for(lambda: not self._buffer and not self._in_flight, timeout)
            finally:
                self._flushing -= 1

    def close(self, timeout: Optional[float] = 5.0) -> None:
        if self._closed:
            return
        if self.url:
            self.flush(timeout)
        with self._cond:
            self._closed = True
            self._cond.notify_all()
        if self._buffer:
            log.warning("Stardust closed with %d unsent events", len(self._buffer))

    # --- worker ------------------------------------------------------------------

    def _run(self) -> None:
        while True:
            with self._cond:
                self._cond.wait_for(lambda: self._buffer or self._closed)
                if self._closed:
                    return
                event = self._buffer.popleft()
                self._in_flight = 1
            ok, retry = self._send(event)
            with self._cond:
                self._in_flight = 0
                if retry:
                    self._buffer.appendleft(event)
                    self._backoff = min(max(self._backoff * 2, 1.0), self.MAX_BACKOFF_S)
                    wait = self._backoff
                else:
                    self._backoff = 0.0
                    wait = 0.0
                self._cond.notify_all()
                if wait and self._flushing:
                    self._cond.wait(self.FLUSH_RETRY_S)
                elif wait:
                    # Woken early by flush() or close().
                    self._cond.wait_for(lambda: self._closed or self._flushing > 0, wait)

    def _send(self, event: Dict[str, Any]) -> Tuple[bool, bool]:
        """Returns (sent, should_retry)."""
        try:
            status, body = self._transport("POST", self.url, json.dumps(event).encode(), self.timeout,
                                           self._headers(True))
        except (OSError, urllib.error.URLError) as e:
            log.debug("Stardust Core unreachable (%s); will retry", type(e).__name__)
            return False, True
        if 200 <= status < 300:
            if self.on_result:
                try:
                    self.on_result(json.loads(body))
                except Exception:  # noqa: BLE001
                    log.exception("Stardust on_result callback failed")
            return True, False
        if status >= 500 or status == 429:
            return False, True
        if status in (401, 403):
            # A missing, wrong or revoked key: retrying won't help, and the app must not notice.
            self.dropped += 1
            if not self._auth_warned:
                self._auth_warned = True
                log.warning(AUTH_WARNING)
            return False, False
        # 4xx: the event itself is bad; retrying won't help.
        log.warning("Stardust Core rejected an event (%s): %s", status, body[:500])
        return False, False

    # --- reading results and remediation (user-initiated: these raise StardustError) ----------

    def _request(self, method: str, path: str, body: Optional[Dict[str, Any]] = None,
                 query: Optional[Dict[str, Any]] = None) -> Any:
        if not self.api_base:
            raise StardustError(0, "this Stardust client has no api_base, so it can't call Core")
        url = self.api_base + path
        q = {k: v for k, v in (query or {}).items() if v is not None}
        if q:
            url += "?" + urlencode(q)
        data = json.dumps(body).encode() if body is not None else None
        try:
            status, raw = self._transport(method, url, data, self.timeout, self._headers(data is not None))
        except (OSError, urllib.error.URLError) as e:
            raise StardustError(0, f"could not reach Stardust Core at {self.api_base} ({type(e).__name__})") from None
        try:
            payload = json.loads(raw) if raw else None
        except ValueError:
            payload = None
        if not 200 <= status < 300:
            detail = payload.get("detail") if isinstance(payload, dict) else None
            reason = payload.get("reason") if isinstance(payload, dict) else None
            if status == 401 and not detail:
                detail = "API key missing or invalid"
            raise StardustError(status, str(detail or raw[:200].decode("utf-8", "replace") or f"HTTP {status}"),
                                reason)
        return payload

    def summary(self, since: TimeArg = None, until: TimeArg = None) -> Summary:
        """Totals for the key's organization (or user): cost, energy, CO2e, water and the indicator code."""
        return Summary.from_dict(self._request("GET", "/v1/summary", query={"since": _iso(since), "until": _iso(until)}))

    def summary_daily(self, since: TimeArg = None, until: TimeArg = None) -> List[DailySummary]:
        """The same totals per UTC day."""
        body = self._request("GET", "/v1/summary/daily", query={"since": _iso(since), "until": _iso(until)})
        return [DailySummary.from_dict(d) for d in body.get("days", [])]

    def request_quote(self, co2e_g: float, tier_preference: Optional[Sequence[str]] = None) -> List[Quote]:
        """Quotes for removing ``co2e_g`` grams of CO2e, cheapest first. A sandbox key gets sandbox quotes."""
        req: Dict[str, Any] = {"co2e_g": co2e_g}
        if tier_preference:
            req["tier_preference"] = list(tier_preference)
        return [Quote.from_dict(q) for q in self._request("POST", "/v1/remediation/quotes", req).get("quotes", [])]

    def submit_order(self, quote_id: Union[str, Quote], payment_ref: Optional[str] = None) -> Order:
        """Place an order for a quote. Each quote can be used once, within 15 minutes."""
        qid = quote_id.quote_id if isinstance(quote_id, Quote) else str(quote_id)
        req: Dict[str, Any] = {"quote_id": qid}
        if payment_ref is not None:
            req["payment_ref"] = payment_ref
        return Order.from_dict(self._request("POST", "/v1/remediation/orders", req))

    def order_status(self, order_id: Union[str, Order]) -> Order:
        """An order's fulfillment status and certificate."""
        oid = order_id.remediation_order_id if isinstance(order_id, Order) else str(order_id)
        return Order.from_dict(self._request("GET", f"/v1/remediation/orders/{_quote(oid, safe='')}"))
