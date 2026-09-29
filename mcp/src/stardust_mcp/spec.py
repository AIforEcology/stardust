"""The spec files generated into schema/ (docs/tools/stardust_telemetry_sync.py export).

Read from the repo root, the same way Core finds its methodology and schema files.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict

SCHEMA_DIR = Path(__file__).resolve().parents[3] / "schema"

# The column that names each kind of code in the spec's tables.
_NAME_KEYS = ("field_name", "meaning", "source_plant_type", "provider_category", "technology_type")


@lru_cache(maxsize=1)
def spec_version() -> str:
    return json.loads((SCHEMA_DIR / "spec-version.json").read_text())["spec_version"]


@lru_cache(maxsize=1)
def field_codes() -> Dict[str, Dict[str, Any]]:
    codes = json.loads((SCHEMA_DIR / "field-codes.json").read_text())["codes"]
    return {c["code"].upper(): c for c in codes}


def code_name(entry: Dict[str, Any]) -> str:
    return next((entry[k] for k in _NAME_KEYS if entry.get(k)), entry["code"])
