"""Allocations: AIforE's estimate of where each provider's compute runs.

No provider says which site served a request, so each provider's compute is split across facilities
with explicit weights (DCI spec v0.1). Every weight is estimated, dated and carries a rationale and
sources. Compute in undisclosed locations gets its own weight on the national fallback grid, so the
gap stays visible.

The allocation method needs board sign-off before any footprint is published (docs/TODO.md).

A component names its facilities either by ledger ID (``{"facility_id": "DC-US-IN-0012"}``, the
stable form) or by a search (``{"match": {"q": "Rainier", "state": "US-IN"}}``) until the ID is
known; the footprint records the ID each search resolved to.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List

ALLOCATIONS_FORMAT = "stardust-dci-allocations"
WEIGHT_TOLERANCE = 1e-6
_FACILITY_ID = re.compile(r"^(DC|PWR)-[A-Z]{2}-[A-Z0-9]{1,3}-[0-9]{4,}$")
_STATE = re.compile(r"^[A-Z]{2}-[A-Z0-9]{1,3}$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def load_allocations(path: Path) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def validate_allocations(a: Dict[str, Any]) -> List[str]:
    """Problems with an allocations file (empty when valid)."""
    out: List[str] = []
    if a.get("format") != ALLOCATIONS_FORMAT:
        out.append(f"format must be {ALLOCATIONS_FORMAT!r}")
    if not _DATE.match(str(a.get("as_of", ""))):
        out.append("as_of must be a date (YYYY-MM-DD)")
    if not str(a.get("fallback_grid", "")).startswith("core:"):
        out.append("fallback_grid must name a Core grid, e.g. core:US")
    providers = a.get("providers")
    if not isinstance(providers, dict) or not providers:
        return out + ["providers must be a non-empty object"]
    for name, p in providers.items():
        where = f"provider {name}"
        if name != name.lower():
            out.append(f"{where}: provider names are lower case")
        comps = p.get("components") if isinstance(p, dict) else None
        if not isinstance(comps, list) or not comps:
            out.append(f"{where}: needs components")
            continue
        ids = [c.get("id") for c in comps]
        if len(set(ids)) != len(ids) or not all(ids):
            out.append(f"{where}: every component needs a unique id")
        total = 0.0
        for c in comps:
            cw = f"{where}, component {c.get('id')}"
            w = c.get("weight")
            if not isinstance(w, (int, float)) or isinstance(w, bool) or not 0 < w <= 1:
                out.append(f"{cw}: weight must be a number in (0, 1]")
            else:
                total += w
            if not c.get("label"):
                out.append(f"{cw}: needs a label")
            if not c.get("rationale"):
                out.append(f"{cw}: needs a rationale (every allocation is an estimate)")
            if not c.get("sources") or not all(isinstance(s, str) and s.startswith("http") for s in c["sources"]):
                out.append(f"{cw}: needs at least one source URL")
            facilities = c.get("facilities")
            if c.get("undisclosed"):
                if facilities:
                    out.append(f"{cw}: an undisclosed component lists no facilities")
            elif not isinstance(facilities, list) or not facilities:
                out.append(f"{cw}: needs facilities, or undisclosed: true")
            else:
                for ref in facilities:
                    out.extend(f"{cw}: {p}" for p in _check_ref(ref))
        if abs(total - 1) > WEIGHT_TOLERANCE:
            out.append(f"{where}: weights add up to {total:g}, not 1")
    return out


def _check_ref(ref: Any) -> List[str]:
    if not isinstance(ref, dict):
        return ["a facility reference must be an object"]
    if "facility_id" in ref:
        return [] if _FACILITY_ID.match(str(ref["facility_id"])) else [f"bad facility_id {ref['facility_id']!r}"]
    m = ref.get("match")
    if not isinstance(m, dict) or not m.get("q"):
        return ["a facility reference needs facility_id or match.q"]
    if m.get("state") is not None and not _STATE.match(str(m["state"])):
        return [f"match.state must be ISO 3166-2 (US-IN), got {m['state']!r}"]
    return []
