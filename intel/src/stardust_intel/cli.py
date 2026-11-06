"""stardust-intel: the DCI commands (Phase 1 runs each step by hand).

    stardust-intel validate                     check the allocations and grids files
    stardust-intel eia-state IN --year 2025     fetch a state's mix and CO2 rate from EIA into the grids file
    stardust-intel footprint -o footprint.json  build the footprint from the ledger
    stardust-intel footprint -o footprint.json --check   fail if that file is stale

The footprint is off in Core until STARDUST_FOOTPRINT_FILE points at it, and is not published
until the board signs off the allocation method and counsel reviews right of reply (docs/TODO.md).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import List, Optional

from .allocations import load_allocations, validate_allocations
from .eia import EIAError, fetch_state_grid, state_code
from .footprint import build_footprint, comparable
from .grids import load_grids, save_grids, validate_grid
from .ledger import DEFAULT_LEDGER_URL, ApiLedger, DbLedger

INTEL_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = INTEL_DIR.parent
DEFAULT_ALLOCATIONS = INTEL_DIR / "data" / "allocations.json"
DEFAULT_GRIDS = INTEL_DIR / "data" / "grids.json"
DEFAULT_METHODOLOGY = REPO_ROOT / "schema" / "factors" / "methodology-v0.2.json"


def _json(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def cmd_validate(args: argparse.Namespace) -> int:
    problems = validate_allocations(load_allocations(args.allocations))
    grids = load_grids(args.grids)
    for key, g in grids["grids"].items():
        problems.extend(validate_grid(key, g))
    core = _json(args.methodology)
    fb = load_allocations(args.allocations).get("fallback_grid", "")
    if fb.startswith("core:") and fb[5:] not in core["grids"]:
        problems.append(f"fallback_grid {fb} is not a grid in {args.methodology.name}")
    for p in problems:
        print(f"invalid: {p}", file=sys.stderr)
    if not problems:
        n = sum(len(p["components"]) for p in load_allocations(args.allocations)["providers"].values())
        print(f"ok: {n} allocation components, {len(grids['grids'])} grids")
    return 1 if problems else 0


def cmd_eia_state(args: argparse.Namespace) -> int:
    key = os.environ.get("EIA_API_KEY")
    if not key:
        print("Set EIA_API_KEY (free: https://www.eia.gov/opendata/register.php).", file=sys.stderr)
        return 2
    grids = load_grids(args.grids)
    for s in args.states:
        try:
            g = fetch_state_grid(s, args.year, key)
        except (EIAError, ValueError) as e:
            print(f"{s}: {e}", file=sys.stderr)
            return 1
        grids["grids"][f"US-{state_code(s)}"] = g
        print(f"US-{state_code(s)} {args.year}: {g['intensity_g_per_kwh']} g/kWh ({g['intensity_basis']}), "
              f"mix {g['mix']}")
    save_grids(args.grids, grids)
    return 0


def cmd_footprint(args: argparse.Namespace) -> int:
    allocations = load_allocations(args.allocations)
    problems = validate_allocations(allocations)
    if problems:
        for p in problems:
            print(f"invalid: {p}", file=sys.stderr)
        return 1
    dsn = args.ledger_dsn or (os.environ.get("LEDGER_DATABASE_URL") if args.ledger_url is None else None)
    ledger = DbLedger(dsn) if dsn else ApiLedger(args.ledger_url or DEFAULT_LEDGER_URL)
    try:
        fp = build_footprint(allocations, load_grids(args.grids), _json(args.methodology), ledger)
    finally:
        ledger.close()
    text = json.dumps(fp, indent=2, ensure_ascii=False) + "\n"
    if args.check:
        if not args.output or not args.output.exists():
            print(f"--check needs an existing footprint file (-o); {args.output} not found", file=sys.stderr)
            return 1
        if comparable(_json(args.output)) != comparable(fp):
            print(f"{args.output} is stale: re-run stardust-intel footprint -o {args.output}", file=sys.stderr)
            return 1
        print(f"{args.output} is up to date")
        return 0
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    for name, p in fp["providers"].items():
        print(f"{name}: {p['intensity_g_per_kwh']} g CO2/kWh ({p['vs_flat_pct']:+.0f}% vs {p['flat_grid']}), "
              f"{p['known_site_share'] * 100:.0f}% at known sites, {len(p['caveats'])} caveats", file=sys.stderr)
    return 0


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="stardust-intel", description="Stardust Data Center Intelligence (DCI)")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--allocations", type=Path, default=DEFAULT_ALLOCATIONS)
    common.add_argument("--grids", type=Path, default=DEFAULT_GRIDS)
    common.add_argument("--methodology", type=Path, default=DEFAULT_METHODOLOGY,
                        help="Core's methodology file (fallback grid and version)")
    sub = ap.add_subparsers(dest="command", required=True)

    sub.add_parser("validate", parents=[common], help="check the allocations and grids files").set_defaults(
        func=cmd_validate)

    e = sub.add_parser("eia-state", parents=[common], help="fetch state mixes from the EIA API v2 (EIA_API_KEY)")
    e.add_argument("states", nargs="+", help="state codes, e.g. IN TN NY")
    e.add_argument("--year", type=int, required=True)
    e.set_defaults(func=cmd_eia_state)

    f = sub.add_parser("footprint", parents=[common], help="build the footprint file from the ledger")
    f.add_argument("-o", "--output", type=Path)
    f.add_argument("--check", action="store_true", help="fail if the output file is stale")
    src = f.add_mutually_exclusive_group()
    src.add_argument("--ledger-url", help=f"ledger API base (default {DEFAULT_LEDGER_URL})")
    src.add_argument("--ledger-dsn", help="ledger database URL (default LEDGER_DATABASE_URL, on the server)")
    f.set_defaults(func=cmd_footprint)
    return ap


def main(argv: Optional[List[str]] = None) -> int:
    args = parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
