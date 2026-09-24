"""Fetch BerriAI/litellm's model pricing file into middleware/data/ (spec §9.4).

The file is MIT-licensed data from https://github.com/BerriAI/litellm. It is
validated before it replaces the current copy, so a malformed or tampered upstream
file can't silently corrupt cost calculations (§14.2).

Usage: python scripts/update_pricing.py [--ref <git ref>]
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from stardust_core.pricing import PricingTable  # noqa: E402

DEST = Path(__file__).resolve().parents[1] / "data" / "model_prices_and_context_window.json"
URL = "https://raw.githubusercontent.com/BerriAI/litellm/{ref}/model_prices_and_context_window.json"
MIN_PRICED_MODELS = 100


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", default="main", help="litellm git ref; pin a commit SHA for reproducible builds")
    args = ap.parse_args()

    url = URL.format(ref=args.ref)
    with urllib.request.urlopen(url, timeout=30) as r:
        body = r.read()
    data = json.loads(body)
    if not isinstance(data, dict):
        print("upstream file is not a JSON object; refusing to update", file=sys.stderr)
        return 1

    tmp = DEST.with_suffix(".tmp")
    tmp.write_bytes(body)
    table = PricingTable.load(tmp)
    if len(table) < MIN_PRICED_MODELS:
        tmp.unlink()
        print(f"only {len(table)} valid priced models (< {MIN_PRICED_MODELS}); refusing to update", file=sys.stderr)
        return 1
    tmp.replace(DEST)
    print(f"updated {DEST.name} from {url}: {len(table)} priced models")
    return 0


if __name__ == "__main__":
    sys.exit(main())
