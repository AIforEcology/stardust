"""Stardust client: queues usage events and sends them to Stardust Core in the background.

Recording never blocks or raises in the caller's code path (spec §15: no perceptible
latency; never a blocked user action). Events are buffered in memory and retried with
backoff while Core is unreachable (§15 offline resilience).
"""

from __future__ import annotations

import atexit
import json
import logging
import threading
import urllib.error
import urllib.request
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Any, Callable, Deque, Dict, Optional, Tuple

from .extract import Usage, extract

log = logging.getLogger("stardust_sdk")

# (url, body, timeout) -> (status, response body)
Transport = Callable[[str, bytes, float], Tuple[int, bytes]]


def _urllib_transport(url: str, body: bytes, timeout: float) -> Tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, method="POST", headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


class Stardust:
    """Report AI usage to Stardust Core.

    >>> stardust = Stardust("http://localhost:8080", region="us-east-1")
    >>> stardust.record_response(anthropic_message)       # or instrument(client, stardust)
    """

    MAX_BACKOFF_S = 60.0
    # While flush() is waiting, retry quickly instead of backing off.
    FLUSH_RETRY_S = 0.05

    def __init__(
        self,
        api_base: str = "http://localhost:8080",
        *,
        user_id: Optional[str] = None,
        org_id: Optional[str] = None,
        region: Optional[str] = None,
        enabled: bool = True,
        max_buffer: int = 10_000,
        timeout: float = 5.0,
        on_result: Optional[Callable[[Dict[str, Any]], None]] = None,
        transport: Optional[Transport] = None,
    ):
        self.url = api_base.rstrip("/") + "/v1/events"
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
        self._worker = threading.Thread(target=self._run, name="stardust-sdk", daemon=True)
        self._worker.start()
        atexit.register(self.close, 2.0)

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
        timestamp: Optional[datetime] = None,
    ) -> None:
        if not self.enabled or self._closed:
            return
        event: Dict[str, Any] = {
            "event_id": str(uuid.uuid4()),
            "source_layer": "infra_agent",
            "provider": provider,
            "model": model,
            "timestamp": (timestamp or datetime.now(timezone.utc)).astimezone().isoformat(),
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "tokens_estimated": False,
        }
        optional = {
            "tokens_cached_in": tokens_cached_in,
            "region": region or self.region,
            "user_id": self.user_id,
            "org_id": self.org_id,
        }
        event.update({k: v for k, v in optional.items() if v is not None})
        with self._cond:
            if len(self._buffer) == self._buffer.maxlen:
                self.dropped += 1  # deque drops the oldest
                log.warning("Stardust buffer full; dropping the oldest event")
            self._buffer.append(event)
            self._cond.notify()

    def record_usage(self, usage: Usage, *, region: Optional[str] = None) -> None:
        self.record(
            usage.provider, usage.model, usage.tokens_in, usage.tokens_out,
            tokens_cached_in=usage.tokens_cached_in, region=region,
        )

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
            status, body = self._transport(self.url, json.dumps(event).encode(), self.timeout)
        except (OSError, urllib.error.URLError) as e:
            log.debug("Stardust Core unreachable (%s); will retry", e)
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
        # 4xx: the event itself is bad; retrying won't help.
        log.warning("Stardust Core rejected an event (%s): %s", status, body[:500])
        return False, False
