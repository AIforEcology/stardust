"""Optional provider footprint overlay from Data Center Intelligence (DCI, intel/).

A footprint file (written by ``stardust-intel footprint``) adds one blended grid per provider,
built from the data center facilities that provider is estimated to run on, and points the
provider's default grid at it. Core loads it only when ``STARDUST_FOOTPRINT_FILE`` is set; without
it nothing here runs and Core behaves exactly as before.

Rules (DCI spec v0.1, "Integration with Stardust Core"):

- Footprint grids are added, never substituted: their keys start with ``fp:`` and may not reuse a
  grid the methodology already defines.
- A region still wins. The footprint only changes events with no region, through
  ``provider_default_grid``.
- Core refuses a footprint that points a provider at a grid it doesn't define (FootprintError at
  startup, so a bad file never serves numbers).
- Estimates are labelled: an event scored on an estimated footprint grid is tagged
  ``confidence_tier: estimated``, and every enriched event carries the methodology version with a
  ``+dci-<version>`` suffix, so any number can be traced to the footprint that produced it.
"""

from __future__ import annotations

import copy
import json
import math
import re
from pathlib import Path
from typing import Any, Dict, Iterable

FORMAT = "stardust-footprint"
GRID_PREFIX = "fp:"
CONFIDENCE = ("measured", "reported", "estimated")
_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
# What /v1/methodology shows of the footprint, for traceability; the grids themselves are merged in.
_META_KEYS = ("dci_version", "as_of", "status", "core_methodology_version", "ledger", "providers")


class FootprintError(ValueError):
    """The footprint file is invalid; Core refuses to start with it."""


def load_footprint(path: Path) -> Dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as f:
            fp = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise FootprintError(f"cannot read footprint file {path}: {e}") from e
    if not isinstance(fp, dict):
        raise FootprintError(f"footprint file {path} must hold a JSON object")
    return fp


def _number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _check_grid(key: str, g: Any, esc_codes: Iterable[str]) -> None:
    where = f"footprint grid {key!r}"
    if not isinstance(g, dict):
        raise FootprintError(f"{where} must be an object")
    if not _number(g.get("intensity_g_per_kwh")) or g["intensity_g_per_kwh"] < 0:
        raise FootprintError(f"{where}: intensity_g_per_kwh must be a number ≥ 0")
    if g.get("confidence") not in CONFIDENCE:
        raise FootprintError(f"{where}: confidence must be one of {', '.join(CONFIDENCE)}")
    mix = g.get("mix")
    if mix is None:
        return
    if not isinstance(mix, dict) or not mix:
        raise FootprintError(f"{where}: mix must be null or an object of energy source code → percent")
    unknown = sorted(set(mix) - set(esc_codes))
    if unknown:
        raise FootprintError(f"{where}: mix uses unknown energy source codes {unknown}")
    if not all(_number(v) and 0 <= v <= 100 for v in mix.values()):
        raise FootprintError(f"{where}: mix shares must be numbers from 0 to 100")
    total = sum(mix.values())
    if abs(total - 100) > 1:
        raise FootprintError(f"{where}: mix adds up to {total:g}%, not 100%")


def apply_footprint(cfg: Dict[str, Any], fp: Dict[str, Any], esc_codes: Iterable[str]) -> Dict[str, Any]:
    """Return a copy of the methodology config with the footprint merged in. ``cfg`` is not changed."""
    esc_codes = list(esc_codes)
    if fp.get("format") != FORMAT:
        raise FootprintError(f"not a footprint file: format must be {FORMAT!r}")
    version = fp.get("dci_version")
    if not isinstance(version, str) or not _VERSION.match(version):
        raise FootprintError("dci_version must be a version like 0.1.0")

    grids = fp.get("grids")
    if not isinstance(grids, dict) or not grids:
        raise FootprintError("footprint has no grids")
    for key, g in grids.items():
        if not key.startswith(GRID_PREFIX) or len(key) == len(GRID_PREFIX):
            raise FootprintError(f"footprint grid {key!r} must be named {GRID_PREFIX}<provider>")
        if key in cfg["grids"]:
            raise FootprintError(f"footprint grid {key!r} would replace a grid the methodology defines")
        _check_grid(key, g, esc_codes)

    defaults = fp.get("provider_default_grid")
    if not isinstance(defaults, dict) or not defaults:
        raise FootprintError("footprint has no provider_default_grid")
    known = set(cfg["grids"]) | set(grids)
    for provider, key in defaults.items():
        if not isinstance(provider, str) or provider != provider.lower() or not provider:
            raise FootprintError(f"provider names must be lower case, got {provider!r}")
        if provider == "default":
            raise FootprintError("a footprint may not change the default grid for unknown providers")
        if key not in known:
            raise FootprintError(f"footprint points provider {provider!r} at grid {key!r}, which Core doesn't define")

    base_version = fp.get("core_methodology_version")
    if base_version is not None and base_version != cfg["version"]:
        raise FootprintError(
            f"footprint was built against methodology {base_version}, but Core runs {cfg['version']}; rebuild it")

    out = copy.deepcopy(cfg)
    for key, g in grids.items():
        out["grids"][key] = {
            "intensity_g_per_kwh": g["intensity_g_per_kwh"],
            "mix": g.get("mix"),
            "citation": g.get("citation", f"Stardust DCI footprint {version}"),
            "confidence": g["confidence"],
            # Methodology.grid_for reads this to tag events scored on this grid as estimated.
            "estimated": g["confidence"] == "estimated",
        }
    out["provider_default_grid"].update(defaults)
    out["version"] = f"{cfg['version']}+dci-{version}"
    out["footprint"] = {k: fp[k] for k in _META_KEYS if k in fp}
    return out
