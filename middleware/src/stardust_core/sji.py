"""Stardust Job Identifier (spec §5.3).

``XXXXXXXX-ESC-XXXX-XXXX-XXXXXXXXXXXX``: a GUID shape whose second segment is the
3-letter Energy Source Code. Deliberately *not* an RFC 4122 UUID; ``event_id``
remains the standard UUID key.
"""

from __future__ import annotations

import re
import uuid

SJI_PATTERN = re.compile(r"^([0-9A-F]{8})-([A-Z]{3})-([0-9A-F]{4})-([0-9A-F]{4})-([0-9A-F]{12})$")


def new_sji(esc: str) -> str:
    if not re.fullmatch(r"[A-Z]{3}", esc):
        raise ValueError(f"Energy Source Code must be 3 uppercase letters, got {esc!r}")
    h = uuid.uuid4().hex.upper()
    return f"{h[0:8]}-{esc}-{h[8:12]}-{h[12:16]}-{h[16:28]}"


def esc_of(sji: str) -> str:
    m = SJI_PATTERN.match(sji)
    if not m:
        raise ValueError(f"Not a Stardust Job Identifier: {sji!r}")
    return m.group(2)
