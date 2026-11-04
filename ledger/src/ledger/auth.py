"""Write keys for the ledger: ``X-Ledger-Key: lk_<key_id>_<secret>``.

Same pattern as Core's keys (middleware/src/stardust_core/auth.py): the key is random and long,
shown once at creation, and only its SHA-256 hash is stored. Reads need no key.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from typing import FrozenSet, Optional, Tuple

KEY_PREFIX = "lk_"
ADMIN_TOKEN_KEY_ID = "admin-token"


@dataclass(frozen=True)
class Principal:
    key_id: str
    scopes: FrozenSet[str]

    def has(self, scope: str) -> bool:
        return scope in self.scopes


class AuthError(Exception):
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


def matches(key: str, stored_hash: str) -> bool:
    return hmac.compare_digest(hash_key(key), stored_hash)


def token_matches(given: Optional[str], expected: Optional[str]) -> bool:
    return bool(given) and bool(expected) and hmac.compare_digest(given, expected)
