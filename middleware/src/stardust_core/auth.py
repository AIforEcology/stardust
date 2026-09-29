"""Subscriber authentication and tenant isolation (spec §23.6; plan step M1 in docs/architecture/mcp.md).

Off by default. ``STARDUST_SUBSCRIBER_AUTH`` chooses the mode:

- ``off``: read endpoints behave exactly as before, and any ``Authorization`` header is ignored.
- ``optional``: a caller that sends a key is held to it; a caller without one is served as before.
  This is the transition mode, so existing clients keep working while they move to keys.
- ``required``: the read endpoints need a key with ``reporting.read``.

A key belongs to one organization and, optionally, one user within it. It is sent as
``Authorization: Bearer <key>``, the same header the MCP servers' OAuth tokens will use, and resolves
to a ``Principal`` that the read endpoints narrow their queries to. Keys are random and long, so a
SHA-256 hash is enough to store them; the secret itself is shown once, at creation.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from typing import FrozenSet, Iterable, Optional, Tuple
from uuid import UUID

AUTH_MODES = ("off", "optional", "required")

# OAuth scopes from spec §23.6. Keys can carry any of them; M1 enforces reporting.read.
REPORTING_READ = "reporting.read"
SCOPES = frozenset({REPORTING_READ, "provider.read", "operator.lifecycle.read", "operator.lifecycle.write"})

KEY_PREFIX = "sdk_"


def parse_auth_mode(value: str) -> str:
    v = value.strip().lower() or "off"
    if v not in AUTH_MODES:
        raise ValueError(f"STARDUST_SUBSCRIBER_AUTH must be one of {', '.join(AUTH_MODES)}, got {value!r}")
    return v


def validate_scopes(scopes: Iterable[str]) -> FrozenSet[str]:
    s = frozenset(scopes)
    if not s:
        raise ValueError("a key needs at least one scope")
    unknown = s - SCOPES
    if unknown:
        raise ValueError(f"unknown scopes {sorted(unknown)}; valid scopes are {sorted(SCOPES)}")
    return s


@dataclass(frozen=True)
class Principal:
    """Who is calling: an organization, optionally one user in it, and what they may do."""

    key_id: str
    org_id: UUID
    user_id: Optional[UUID]
    scopes: FrozenSet[str]


class AuthError(Exception):
    """``status`` is the HTTP status; ``reason`` is a §23.7 reason code."""

    def __init__(self, status: int, reason: str, message: str):
        super().__init__(message)
        self.status, self.reason, self.message = status, reason, message


def new_key() -> Tuple[str, str, str]:
    """Returns (key_id, full key to give the caller once, hash to store)."""
    key_id = secrets.token_hex(8)
    key = f"{KEY_PREFIX}{key_id}_{secrets.token_urlsafe(32)}"
    return key_id, key, hash_key(key)


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def split_key(key: str) -> Optional[str]:
    """The key_id inside a well-formed key, else None."""
    if not key.startswith(KEY_PREFIX):
        return None
    key_id, sep, secret = key[len(KEY_PREFIX):].partition("_")
    return key_id if sep and key_id and secret else None


def bearer(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    return token.strip() if scheme.lower() == "bearer" and token.strip() else None


def matches(key: str, stored_hash: str) -> bool:
    return hmac.compare_digest(hash_key(key), stored_hash)


def narrow(principal: Optional[Principal], scope: str, user_id: Optional[UUID],
           org_id: Optional[UUID]) -> Tuple[Optional[UUID], Optional[UUID]]:
    """The (user_id, org_id) a query may actually use.

    Without a principal (auth off, or optional with no key) the request's own filters stand, as
    before. With one, the query is pinned to the principal's organization, and to its user if the
    key is user-level. Asking for another organization or user is refused, never silently widened.
    """
    if principal is None:
        return user_id, org_id
    if scope not in principal.scopes:
        raise AuthError(403, "forbidden_scope", f"this key lacks the {scope} scope")
    if org_id is not None and org_id != principal.org_id:
        raise AuthError(403, "forbidden_scope", "this key can't read another organization")
    if principal.user_id is not None:
        if user_id is not None and user_id != principal.user_id:
            raise AuthError(403, "forbidden_scope", "this key can't read another user")
        user_id = principal.user_id
    return user_id, principal.org_id
