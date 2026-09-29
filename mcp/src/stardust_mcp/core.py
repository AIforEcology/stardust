"""A small async client for the Stardust Core REST API, as the MCP servers use it.

The servers never import Core: they call it over HTTP with the subscriber's key, so Core's tenant
isolation (middleware README, "Subscriber API keys") applies to everything they return.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import httpx2

# Reason codes from spec §23.7, by the HTTP status Core answers with.
_REASONS = {400: "invalid_request", 401: "unauthorized", 403: "forbidden_scope", 404: "not_found",
            422: "invalid_period", 429: "rate_limited"}


class CoreError(Exception):
    def __init__(self, status: int, reason: str, message: str):
        super().__init__(message)
        self.status, self.reason, self.message = status, reason, message


class CoreClient:
    def __init__(self, base_url: str, http: Optional[httpx2.AsyncClient] = None, timeout_s: float = 15.0):
        self.base_url = base_url.rstrip("/")
        self.http = http or httpx2.AsyncClient(timeout=timeout_s)

    async def aclose(self) -> None:
        await self.http.aclose()

    async def _call(self, method: str, path: str, key: str, **kw: Any) -> Dict[str, Any]:
        try:
            r = await self.http.request(method, self.base_url + path,
                                        headers={"Authorization": f"Bearer {key}"}, **kw)
        except httpx2.HTTPError as e:
            raise CoreError(503, "core_unavailable", f"Stardust Core is unreachable: {e}") from e
        if r.status_code >= 400:
            try:
                body = r.json()
            except ValueError:
                body = {}
            detail = body.get("detail") if isinstance(body, dict) else None
            reason = (body.get("reason") if isinstance(body, dict) else None) or _REASONS.get(r.status_code, "error")
            raise CoreError(r.status_code, reason, detail if isinstance(detail, str) else f"Core returned {r.status_code}")
        return r.json()

    @staticmethod
    def _period(since: Optional[str], until: Optional[str], user_id: Optional[str]) -> Dict[str, str]:
        params = {"since": since, "until": until, "user_id": user_id}
        return {k: v for k, v in params.items() if v is not None}

    async def principal(self, key: str) -> Dict[str, Any]:
        return await self._call("GET", "/v1/auth/principal", key)

    async def summary(self, key: str, since: Optional[str] = None, until: Optional[str] = None,
                      user_id: Optional[str] = None) -> Dict[str, Any]:
        return await self._call("GET", "/v1/summary", key, params=self._period(since, until, user_id))

    async def daily(self, key: str, since: Optional[str] = None, until: Optional[str] = None,
                    user_id: Optional[str] = None) -> Dict[str, Any]:
        return await self._call("GET", "/v1/summary/daily", key, params=self._period(since, until, user_id))

    async def quote(self, key: str, co2e_g: float) -> Dict[str, Any]:
        return await self._call("POST", "/v1/remediation/quotes", key, json={"co2e_g": co2e_g})
