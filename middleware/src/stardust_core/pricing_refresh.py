"""Background pricing refresh (spec §9.4, §14.2).

Fetches litellm's current pricing file, validates it, compares it with the prices in
use, and swaps it in without a restart. The fetched copy is saved to a cache file
(not the committed, vendored copy), and Core prefers that cache on its next start.
A failed or suspicious fetch never replaces good prices.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import httpx

from .pricing import PricingTable

log = logging.getLogger(__name__)

# A real litellm file prices thousands of models; far fewer means a truncated or wrong file.
MIN_PRICED_MODELS = 100
# Refuse a new file that drops more than this share of the models we price today.
MAX_REMOVED_SHARE = 0.25
# How many model names to keep per change list in the status report.
STATUS_SAMPLE = 20


class PricingRejected(Exception):
    """The fetched file failed validation; current prices were kept."""


def validate(raw: Any, current: PricingTable, source: str) -> PricingTable:
    table = PricingTable.from_raw(raw, source=source)
    if len(table) < MIN_PRICED_MODELS:
        raise PricingRejected(f"only {len(table)} valid priced models (minimum {MIN_PRICED_MODELS})")
    if len(current):
        removed = len(current.diff(table)["removed"])
        if removed / len(current) > MAX_REMOVED_SHARE:
            raise PricingRejected(f"would remove {removed} of {len(current)} priced models")
    return table


def load_initial(vendored: Path, cache: Optional[Path]) -> PricingTable:
    """Prefer a previously refreshed cache, fall back to the vendored copy."""
    if cache and cache.exists():
        try:
            table = PricingTable.load(cache)
            if len(table) >= MIN_PRICED_MODELS:
                return table
            log.warning("Ignoring pricing cache %s: only %d priced models", cache, len(table))
        except (ValueError, OSError) as e:
            log.warning("Ignoring unreadable pricing cache %s: %s", cache, e)
    return PricingTable.load(vendored)


class PricingRefresher:
    def __init__(
        self,
        get_table: Callable[[], PricingTable],
        set_table: Callable[[PricingTable], None],
        http: httpx.AsyncClient,
        url: str,
        cache_path: Optional[Path],
    ):
        self._get, self._set = get_table, set_table
        self._http = http
        self.url = url
        self.cache_path = cache_path
        # Created on first use: on Python 3.9 a Lock binds to the loop current at construction.
        self._lock: Optional[asyncio.Lock] = None
        self.status: Dict[str, Any] = {
            "source": get_table().source,
            "priced_models": len(get_table()),
            "url": url,
            "last_attempt": None,
            "last_success": None,
            "last_error": None,
            "last_changes": None,
            "schedule": "not started",
        }

    async def refresh_once(self) -> Dict[str, Any]:
        """Fetch, validate, diff and swap. Returns the status; never raises for fetch/validation errors."""
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            now = datetime.now(timezone.utc).isoformat()
            self.status["last_attempt"] = now
            try:
                r = await self._http.get(self.url, timeout=60)
                r.raise_for_status()
                raw = r.json()
                current = self._get()
                table = validate(raw, current, source=self.url)
                changes = current.diff(table)
                self._save_cache(raw)
                self._set(table)
            except (httpx.HTTPError, ValueError, PricingRejected, OSError) as e:
                # ValueError covers bad JSON and a non-object file.
                self.status["last_error"] = f"{type(e).__name__}: {e}"
                log.warning("Pricing refresh failed, keeping current prices: %s", e)
                return self.status

            self.status.update(
                source=table.source,
                priced_models=len(table),
                last_success=now,
                last_error=None,
                last_changes={
                    k: {"count": len(v), "sample": v[:STATUS_SAMPLE]} for k, v in changes.items()
                },
            )
            log.info(
                "Pricing refreshed from %s: %d models (%d added, %d removed, %d re-priced)",
                self.url, len(table), len(changes["added"]), len(changes["removed"]), len(changes["changed"]),
            )
            return self.status

    async def run(self, interval_hours: float) -> None:
        """Refresh now, then every ``interval_hours`` (0 = just once)."""
        self.status["schedule"] = "once" if interval_hours == 0 else f"every {interval_hours:g} h"
        while True:
            await self.refresh_once()
            if interval_hours == 0:
                return
            await asyncio.sleep(interval_hours * 3600)

    def _save_cache(self, raw: Any) -> None:
        if not self.cache_path:
            return
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.cache_path.with_name(self.cache_path.name + ".tmp")
        tmp.write_text(json.dumps(raw), encoding="utf-8")
        os.replace(tmp, self.cache_path)
