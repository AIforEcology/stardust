"""Build the provider footprint file that Stardust Core loads with STARDUST_FOOTPRINT_FILE.

For each provider: I_provider = Σ_f w_f · I_grid(f), with Σ w_f = 1 (DCI spec v0.1). The weights
come from the allocations file (AIforE's estimates), the facilities from the ledger, and each
facility's grid from its state's EIA record (grids file). Anything that can't be placed falls back
to Core's national grid, and the footprint says so in its caveats.

Output format ("stardust-footprint"), read by middleware/src/stardust_core/footprint.py:

- ``grids``: one ``fp:<provider>`` grid each, with blended intensity and mix, confidence estimated.
- ``provider_default_grid``: points each provider at its grid. Events naming a region still use
  that region's grid in Core.
- ``providers``: the working: components, facilities, power sources, caveats. Core shows it on
  /v1/methodology but computes nothing from it.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any, Dict, List, Optional, Tuple

from . import __version__
from .grids import ESC_CODES

FORMAT = "stardust-footprint"
STATUS = ("unpublished: the allocation method awaits board sign-off and the right-of-reply process awaits "
          "counsel review (DCI spec v0.1, Roadmap)")
# Fields that change without changing any number; `footprint --check` ignores them.
VOLATILE = ("generated_on", "ledger")
ONSITE_GROUP = "Power: on-site"
SOURCES_GROUP = "Power: sources"


def _state_key(state: Optional[str]) -> Optional[str]:
    if not state:
        return None
    s = state.upper()
    return s if "-" in s else f"US-{s}"


class _Grids:
    """Grid lookup across the EIA grids file and Core's own grids (``core:<KEY>``)."""

    def __init__(self, grids: Dict[str, Any], core_cfg: Dict[str, Any]):
        self.grids = grids.get("grids", {})
        self.core = core_cfg

    def has(self, key: str) -> bool:
        if key.startswith("core:"):
            return key[5:] in self.core["grids"]
        return key in self.grids

    def get(self, key: str) -> Dict[str, Any]:
        if key.startswith("core:"):
            g = self.core["grids"][key[5:]]
            return {"label": f"Core {key[5:]} grid (methodology {self.core['version']})",
                    "intensity_g_per_kwh": float(g["intensity_g_per_kwh"]), "mix": g.get("mix"),
                    "confidence": "estimated" if g.get("needs_verification") else "reported",
                    "intensity_basis": "core", "citation": g.get("citation")}
        return self.grids[key]


def _tenant_match(facility: Dict[str, Any], names: List[str]) -> bool:
    tenant = facility.get("tenant") or ""
    return any(re.search(rf"\b{re.escape(n)}\b", tenant, re.I) for n in names)


def _resolve(ledger: Any, ref: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], str]:
    """A facility reference → (ledger facility detail, how it was found or why not)."""
    if "facility_id" in ref:
        d = ledger.facility(ref["facility_id"])
        return (d, "by ID") if d else (None, f"{ref['facility_id']} is not in the ledger")
    m = ref["match"]
    # Compute runs in data centers; power plants (PWR-) serving them often share the campus name.
    hits = [h for h in ledger.search(q=m["q"], state=_state_key(m.get("state"))) if h["facility_id"].startswith("DC-")]
    if len(hits) != 1:
        where = f" in {m['state']}" if m.get("state") else ""
        return None, f"search {m['q']!r}{where} matched {len(hits)} data centers, not 1"
    d = ledger.facility(hits[0]["facility_id"])
    return d, f"by search {m['q']!r}"


def _power(detail: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Power plants linked to the facility, and its latest on-site and power-source observations."""
    fid = detail["facility_id"]
    plants = [{"facility_id": link["from_facility_id"], "relation": link.get("relation"),
               "link_status": link.get("link_status"), "source_url": link.get("source_url")}
              for link in detail.get("links", []) if link.get("to_facility_id") == fid]

    def obs(group: str) -> List[Dict[str, Any]]:
        return [{k: o.get(k) for k in ("obs_id", "field", "value", "unit", "source_url", "source_date", "confidence",
                                       "review_status")}
                for o in detail.get("latest_observations", []) if o.get("field_group") == group]

    return plants, obs(ONSITE_GROUP), obs(SOURCES_GROUP)


def _pct(x: float) -> str:
    return f"{x * 100:.0f}%"


def build_provider(name: str, spec: Dict[str, Any], fallback: str, grids: _Grids, ledger: Any,
                   core_cfg: Dict[str, Any]) -> Dict[str, Any]:
    tenant_names = spec.get("tenant_names") or [name]
    caveats: List[str] = []
    notes: List[str] = []
    components: List[Dict[str, Any]] = []
    parts: List[Tuple[float, str]] = []  # (share of the provider's compute, grid key)
    placed_ids: set = set()
    known_share = 0.0

    for c in spec["components"]:
        w = float(c["weight"])
        out: Dict[str, Any] = {"id": c["id"], "label": c["label"], "weight": w, "confidence": "estimated",
                               "rationale": c["rationale"], "sources": c["sources"], "facilities": []}
        if c.get("undisclosed"):
            parts.append((w, fallback))
            out["grid"] = fallback
            caveats.append(f"{_pct(w)} of {name}'s compute is at undisclosed sites ({c['label']}), scored on "
                           f"the national grid ({fallback}).")
            components.append(out)
            continue

        refs = c["facilities"]
        resolved = [_resolve(ledger, ref) for ref in refs]
        mws = [(d or {}).get("operational_mw") for d, _ in resolved]
        if all(d for d, _ in resolved) and all(isinstance(m, (int, float)) and m > 0 for m in mws):
            split = [m / sum(mws) for m in mws]
            out["split"] = "operational_mw"
        else:
            split = [1 / len(refs)] * len(refs)
            out["split"] = "equal"

        for ref, (d, how), s in zip(refs, resolved, split):
            share = w * s
            if d is None:
                parts.append((share, fallback))
                out.setdefault("unresolved", []).append({"ref": ref, "reason": how, "share": share,
                                                         "grid": fallback})
                caveats.append(f"{c['label']}: {how}; its {_pct(share)} uses the national grid ({fallback}) "
                               f"until the facility is identified in the ledger.")
                continue
            fid = d["facility_id"]
            placed_ids.add(fid)
            known_share += share
            if how.startswith("by search"):
                notes.append(f"{c['label']}: found {fid} {how}; pin facility_id in the allocations file.")
            state = _state_key(d.get("state"))
            grid_key = state if state and grids.has(state) else fallback
            if grid_key == fallback:
                caveats.append(f"{d.get('name', fid)} ({fid}): no grid record for {state}; its {_pct(share)} "
                               f"uses the national grid ({fallback}). Run stardust-intel eia-state {state}.")
            elif grids.get(grid_key).get("intensity_basis") == "computed":
                caveats.append(f"Grid {grid_key}: intensity computed from the generation mix (estimated); EIA has "
                               f"not reported one for {grids.get(grid_key).get('year')}.")
            parts.append((share, grid_key))
            plants, onsite, sources = _power(d)
            for o in onsite:
                caveats.append(f"{d.get('name', fid)} ({fid}): on-site generation reported ({o['field']}: "
                               f"{o.get('value') or 'not stated'}, {o.get('source_url')}) but not quantified; "
                               f"carbon is likely understated.")
            if not _tenant_match(d, tenant_names):
                caveats.append(f"{d.get('name', fid)} ({fid}): the ledger does not list {name} as a tenant "
                               f"(tenant: {d.get('tenant') or 'blank'}); the weight rests on the rationale.")
            out["facilities"].append({
                "facility_id": fid, "found": how, "name": d.get("name"), "operator": d.get("operator"),
                "tenant": d.get("tenant"), "status": d.get("status"), "state": state,
                "grid_region": d.get("grid_region"), "utility": d.get("utility"),
                "operational_mw": d.get("operational_mw"), "planned_mw": d.get("planned_mw"),
                "share": share, "grid": grid_key, "power_plants": plants, "onsite_power": onsite,
                "power_sources": sources,
            })
        components.append(out)

    # Facilities the ledger ties to this provider that no component weights.
    seen: Dict[str, Dict[str, Any]] = {}
    for n in tenant_names:
        for f in ledger.search(q=n):
            fid = f["facility_id"]
            if fid.startswith("DC-") and fid not in placed_ids and _tenant_match(f, tenant_names):
                seen[fid] = f
    if seen:
        ids = sorted(seen)
        more = f" and {len(ids) - 10} more" if len(ids) > 10 else ""
        caveats.append(f"{len(ids)} ledger facilities list {name} as a tenant but have no allocation weight: "
                       f"{', '.join(ids[:10])}{more}.")

    intensity = sum(s * float(grids.get(k)["intensity_g_per_kwh"]) for s, k in parts)
    mix_total: Dict[str, float] = {}
    mixed = 0.0
    for s, k in parts:
        mix = grids.get(k).get("mix")
        if not mix:
            continue
        mixed += s
        for esc, pct in mix.items():
            mix_total[esc] = mix_total.get(esc, 0.0) + s * pct
    mix_out: Optional[Dict[str, float]] = None
    if mixed > 0:
        mix_out = {e: round(v / mixed, 1) for e, v in sorted(mix_total.items(), key=lambda kv: -kv[1])}
        if mixed < 1 - 1e-9:
            caveats.append(f"Grid mix unknown for {_pct(1 - mixed)} of the blend; the mix shown covers the rest.")

    flat_key = core_cfg["provider_default_grid"].get(name, core_cfg["provider_default_grid"]["default"])
    flat = float(core_cfg["grids"][flat_key]["intensity_g_per_kwh"])
    return {
        "grid": f"fp:{name}",
        "intensity_g_per_kwh": round(intensity, 1),
        "mix": mix_out,
        "confidence": "estimated",
        "flat_grid": flat_key,
        "flat_intensity_g_per_kwh": flat,
        "vs_flat_pct": round((intensity / flat - 1) * 100, 1),
        "known_site_share": round(known_share, 4),
        "undisclosed_share": round(1 - known_share, 4),
        "components": components,
        "caveats": list(dict.fromkeys(caveats)),
        "notes": list(dict.fromkeys(notes)),
    }


def build_footprint(allocations: Dict[str, Any], grids: Dict[str, Any], core_cfg: Dict[str, Any], ledger: Any,
                    today: Optional[dt.date] = None) -> Dict[str, Any]:
    fallback = allocations["fallback_grid"]
    g = _Grids(grids, core_cfg)
    if not g.has(fallback):
        raise ValueError(f"fallback grid {fallback} is not defined in Core's methodology")
    providers = {name: build_provider(name, spec, fallback, g, ledger, core_cfg)
                 for name, spec in allocations["providers"].items()}
    for name, p in providers.items():
        if p["mix"] and set(p["mix"]) - set(ESC_CODES):
            raise ValueError(f"{name}: mix has codes Core doesn't know")
    return {
        "format": FORMAT,
        "dci_version": __version__,
        "as_of": allocations["as_of"],
        "generated_on": (today or dt.date.today()).isoformat(),
        "status": STATUS,
        "core_methodology_version": core_cfg["version"],
        "ledger": ledger.describe(),
        "grids": {p["grid"]: {
            "intensity_g_per_kwh": p["intensity_g_per_kwh"], "mix": p["mix"], "confidence": "estimated",
            "citation": (f"Stardust DCI {__version__} footprint for {name} as of {allocations['as_of']}: "
                         f"{_pct(p['known_site_share'])} of compute at known sites; estimated allocation"),
        } for name, p in providers.items()},
        "provider_default_grid": {name: p["grid"] for name, p in providers.items()},
        "providers": providers,
    }


def comparable(fp: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in fp.items() if k not in VOLATILE}
