"""Locate and load Stardust's versioned configuration (spec §8.4).

All factors live in ``schema/factors`` at the repo root, never in code. Paths can
be overridden with environment variables so a self-hosted Core can ship its own.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional

_REPO_ROOT = Path(__file__).resolve().parents[3]

DEFAULT_METHODOLOGY = _REPO_ROOT / "schema" / "factors" / "methodology-v0.1.json"
DEFAULT_ESC = _REPO_ROOT / "schema" / "esc.json"
DEFAULT_PRICING = _REPO_ROOT / "middleware" / "data" / "model_prices_and_context_window.json"
DEFAULT_DONATION_URL = "https://www.paypal.com/ncp/payment/BRVT8CAGDWN8J"


@dataclass(frozen=True)
class Settings:
    methodology_path: Path
    esc_path: Path
    pricing_path: Path
    donation_url: str
    admin_token: Optional[str]

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            methodology_path=Path(os.environ.get("STARDUST_METHODOLOGY_FILE", DEFAULT_METHODOLOGY)),
            esc_path=Path(os.environ.get("STARDUST_ESC_FILE", DEFAULT_ESC)),
            pricing_path=Path(os.environ.get("STARDUST_PRICING_FILE", DEFAULT_PRICING)),
            donation_url=os.environ.get("STARDUST_DONATION_URL", DEFAULT_DONATION_URL),
            # Admin (provider vetting) endpoints are disabled unless a token is set.
            admin_token=os.environ.get("STARDUST_ADMIN_TOKEN") or None,
        )


def load_json(path: Path) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=8)
def load_methodology(path: Path = DEFAULT_METHODOLOGY) -> Dict[str, Any]:
    return load_json(path)
