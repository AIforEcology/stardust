"""Bearer-token checking for the MCP servers.

Plan step M2a: the bearer token is a Stardust Core API key, checked against Core's
``GET /v1/auth/principal``. Core refuses that lookup when its subscriber auth is off, so a server
pointed at an unprotected Core accepts no one rather than serving every organization's data.
OAuth sign-in (step M2b) replaces this verifier; the tools don't change.
"""

from __future__ import annotations

import logging
from typing import Optional

from mcp.server.auth.provider import AccessToken

from .core import CoreClient, CoreError

log = logging.getLogger("stardust_mcp")


class CoreKeyVerifier:
    """An MCP ``TokenVerifier`` that asks Core who a key belongs to."""

    def __init__(self, core: CoreClient):
        self.core = core

    async def verify_token(self, token: str) -> Optional[AccessToken]:
        try:
            p = await self.core.principal(token)
        except CoreError as e:
            if e.status != 401:
                log.warning("Key check failed: %s (%s)", e.message, e.reason)
            return None
        return AccessToken(
            token=token,
            client_id=p["key_id"],
            scopes=p["scopes"],
            subject=p["user_id"] or p["org_id"],
            claims={"org_id": p["org_id"], "user_id": p["user_id"]},
        )
