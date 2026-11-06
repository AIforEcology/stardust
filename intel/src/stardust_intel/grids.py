"""Grid records: a region's generation mix and carbon intensity, with where each number came from.

A grid's intensity is the regulator's reported figure where one exists for that year (EIA state
profiles: CO2 lb/MWh). Otherwise it is computed from the generation mix with EIA combustion factors,
and marked estimated (DCI spec v0.1, "Provenance, confidence and allocation").

Mixes are keyed by Stardust Energy Source Codes (schema/esc.json), the same keys Core uses. Coal has
no ESC yet, so it is recorded as FPP, as in Core's methodology file.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional

GRIDS_FORMAT = "stardust-dci-grids"

# EIA combustion factors, g CO2 per kWh generated (DCI spec v0.1). Nuclear and renewables are zero.
COMBUSTION_G_PER_KWH = {"coal": 1043.0, "oil": 1088.0, "gas": 440.0}

LB_PER_MWH_TO_G_PER_KWH = 0.45359237  # 1 lb/MWh = 453.59237 g / 1000 kWh

# EIA-923 fuel type IDs that partition "ALL" (no double counting): Stardust ESC and combustion class.
# Pumped storage (HPS) is left out: it stores grid power and its net generation is negative.
EIA_FUELS: Dict[str, Any] = {
    "COW": ("FPP", "coal"),   # all coal products
    "NG": ("NGP", "gas"),     # natural gas
    "OOG": ("FPP", "gas"),    # other gases (blast furnace, refinery and still gas)
    "PET": ("OPP", "oil"),    # petroleum liquids and petroleum coke
    "NUC": ("NPP", None),
    "HYC": ("HYD", None),     # conventional hydroelectric
    "WND": ("WPP", None),
    "SUN": ("SPP", None),     # utility-scale solar
    "GEO": ("GPP", None),
    "BIO": ("REP", None),     # biomass and waste (wood, landfill gas, MSW)
    "OTH": ("UNK", None),
}

ESC_CODES = ("NGP", "RNG", "OPP", "NPP", "SMR", "WEC", "HYD", "SPP", "WPP", "GPP", "FPP", "ZEP", "REP", "UNK")
CONFIDENCE = ("measured", "reported", "estimated")


def mix_from_generation(generation: Dict[str, float]) -> Dict[str, float]:
    """EIA generation by fuel (any unit) → ESC percent shares, one decimal."""
    by_esc: Dict[str, float] = {}
    for fuel, value in generation.items():
        if fuel not in EIA_FUELS or value is None or value <= 0:
            continue
        esc = EIA_FUELS[fuel][0]
        by_esc[esc] = by_esc.get(esc, 0.0) + value
    total = sum(by_esc.values())
    if total <= 0:
        raise ValueError("no positive generation to build a mix from")
    shares = {k: round(v / total * 100, 1) for k, v in sorted(by_esc.items(), key=lambda kv: -kv[1])}
    return {k: v for k, v in shares.items() if v > 0}


def computed_intensity(generation: Dict[str, float]) -> float:
    """g CO2/kWh from generation by EIA fuel and the combustion factors."""
    total = co2 = 0.0
    for fuel, value in generation.items():
        if fuel not in EIA_FUELS or value is None or value <= 0:
            continue
        total += value
        kind = EIA_FUELS[fuel][1]
        if kind:
            co2 += value * COMBUSTION_G_PER_KWH[kind]
    if total <= 0:
        raise ValueError("no positive generation to compute an intensity from")
    return round(co2 / total, 1)


def load_grids(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"format": GRIDS_FORMAT, "grids": {}}
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if data.get("format") != GRIDS_FORMAT:
        raise ValueError(f"{path} is not a DCI grids file (format {GRIDS_FORMAT!r})")
    return data


def save_grids(path: Path, data: Dict[str, Any]) -> None:
    data = {"format": GRIDS_FORMAT, "grids": dict(sorted(data["grids"].items()))}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def validate_grid(key: str, g: Dict[str, Any]) -> List[str]:
    """Problems with one grid record (empty when valid)."""
    out: List[str] = []
    where = f"grid {key}"
    v = g.get("intensity_g_per_kwh")
    if not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) or v < 0:
        out.append(f"{where}: intensity_g_per_kwh must be a number ≥ 0")
    if g.get("confidence") not in CONFIDENCE:
        out.append(f"{where}: confidence must be one of {', '.join(CONFIDENCE)}")
    if g.get("intensity_basis") not in ("reported", "computed"):
        out.append(f"{where}: intensity_basis must be reported or computed")
    if g.get("intensity_basis") == "computed" and g.get("confidence") != "estimated":
        out.append(f"{where}: a computed intensity is estimated")
    mix: Optional[Dict[str, float]] = g.get("mix")
    if mix is not None:
        bad = sorted(set(mix) - set(ESC_CODES))
        if bad:
            out.append(f"{where}: unknown energy source codes {bad}")
        total = sum(mix.values())
        if abs(total - 100) > 1:
            out.append(f"{where}: mix adds up to {total:g}%, not 100%")
    sources = g.get("sources") or []
    if not sources or not all(isinstance(s, dict) and s.get("url") for s in sources):
        out.append(f"{where}: needs at least one source with a url")
    if not g.get("as_of"):
        out.append(f"{where}: needs as_of")
    return out
