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
from typing import Any, Dict, Optional, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[3]

DEFAULT_METHODOLOGY = _REPO_ROOT / "schema" / "factors" / "methodology-v0.1.json"
DEFAULT_ESC = _REPO_ROOT / "schema" / "esc.json"
DEFAULT_PRICING = _REPO_ROOT / "middleware" / "data" / "model_prices_and_context_window.json"
DEFAULT_PRICING_CACHE = _REPO_ROOT / "middleware" / "data" / "cache" / "model_prices_and_context_window.json"
DEFAULT_PRICING_URL = "https://raw.githubusercontent.com/BerriAI/litellm/{ref}/model_prices_and_context_window.json"
DEFAULT_DONATION_URL = "https://www.paypal.com/ncp/payment/BRVT8CAGDWN8J"


def parse_refresh(value: str) -> Optional[float]:
    """``STARDUST_PRICING_REFRESH``: "off" → None, "once" → 0, otherwise a positive number of hours."""
    v = value.strip().lower()
    if v in ("off", "none", "false", "0", ""):
        return None
    if v == "once":
        return 0.0
    hours = float(v)
    if hours <= 0:
        raise ValueError(f"STARDUST_PRICING_REFRESH must be off, once or a positive number of hours, got {value!r}")
    return hours


def parse_headers(value: str) -> Tuple[Tuple[str, str], ...]:
    """``STARDUST_OTLP_HEADERS``: "key=value,key2=value2", the same format as OTEL_EXPORTER_OTLP_HEADERS."""
    pairs = []
    for item in value.split(","):
        if "=" in item:
            k, v = item.split("=", 1)
            if k.strip():
                pairs.append((k.strip(), v.strip()))
    return tuple(pairs)


def parse_protocol(value: str) -> str:
    v = value.strip().lower()
    if v in ("http", "http/protobuf"):
        return "http/protobuf"
    if v == "grpc":
        return "grpc"
    raise ValueError(f"STARDUST_OTLP_PROTOCOL must be http/protobuf or grpc, got {value!r}")


def parse_signals(value: str) -> Tuple[str, ...]:
    signals = tuple(s.strip().lower() for s in value.split(",") if s.strip())
    unknown = set(signals) - {"traces", "metrics"}
    if unknown:
        raise ValueError(f"STARDUST_OTLP_SIGNALS supports traces and metrics, got {sorted(unknown)}")
    return signals


@dataclass(frozen=True)
class Settings:
    methodology_path: Path
    esc_path: Path
    pricing_path: Path
    donation_url: str
    admin_token: Optional[str]
    # None = never refresh pricing, 0 = refresh once at startup, >0 = every N hours.
    pricing_refresh_hours: Optional[float] = None
    pricing_url: str = DEFAULT_PRICING_URL
    pricing_ref: str = "main"
    pricing_cache_path: Optional[Path] = None
    # OTLP export: base endpoint (e.g. http://collector:4318 for HTTP, http://collector:4317 for gRPC); None = off.
    otlp_endpoint: Optional[str] = None
    otlp_headers: Tuple[Tuple[str, str], ...] = ()
    otlp_protocol: str = "http/protobuf"
    otlp_signals: Tuple[str, ...] = ("traces", "metrics")
    otlp_metrics_interval_s: float = 60.0
    # OTLP/gRPC receiver listen address (e.g. 0.0.0.0:4317); None = off. The HTTP receiver is always on.
    otlp_grpc_listen: Optional[str] = None

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            methodology_path=Path(os.environ.get("STARDUST_METHODOLOGY_FILE", DEFAULT_METHODOLOGY)),
            esc_path=Path(os.environ.get("STARDUST_ESC_FILE", DEFAULT_ESC)),
            pricing_path=Path(os.environ.get("STARDUST_PRICING_FILE", DEFAULT_PRICING)),
            donation_url=os.environ.get("STARDUST_DONATION_URL", DEFAULT_DONATION_URL),
            # Admin (provider vetting) endpoints are disabled unless a token is set.
            admin_token=os.environ.get("STARDUST_ADMIN_TOKEN") or None,
            pricing_refresh_hours=parse_refresh(os.environ.get("STARDUST_PRICING_REFRESH", "once")),
            pricing_url=os.environ.get("STARDUST_PRICING_URL", DEFAULT_PRICING_URL),
            pricing_ref=os.environ.get("STARDUST_PRICING_REF", "main"),
            pricing_cache_path=Path(os.environ.get("STARDUST_PRICING_CACHE", DEFAULT_PRICING_CACHE)),
            otlp_endpoint=os.environ.get("STARDUST_OTLP_ENDPOINT") or None,
            otlp_headers=parse_headers(os.environ.get("STARDUST_OTLP_HEADERS", "")),
            otlp_protocol=parse_protocol(os.environ.get("STARDUST_OTLP_PROTOCOL", "http/protobuf")),
            otlp_signals=parse_signals(os.environ.get("STARDUST_OTLP_SIGNALS", "traces,metrics")),
            otlp_metrics_interval_s=float(os.environ.get("STARDUST_OTLP_METRICS_INTERVAL", "60")),
            otlp_grpc_listen=os.environ.get("STARDUST_OTLP_GRPC_LISTEN") or None,
        )


def load_json(path: Path) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=8)
def load_methodology(path: Path = DEFAULT_METHODOLOGY) -> Dict[str, Any]:
    return load_json(path)
