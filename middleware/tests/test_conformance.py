"""Conformance with the spec: the code must agree with the files generated from it.

schema/spec-version.json, field-codes.json and mcp-tools.json are exported from the spec by
docs/tools/stardust_telemetry_sync.py. These tests compare what the code implements against them.
Codes the spec defines but the code doesn't implement yet are reported as a warning, not a failure,
so the gap stays visible while CI stays green.
"""

import json
import re
import warnings
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCHEMA_DIR = REPO / "schema"

SPEC = json.loads((SCHEMA_DIR / "spec-version.json").read_text())
CODES = json.loads((SCHEMA_DIR / "field-codes.json").read_text())["codes"]
TOOLS = json.loads((SCHEMA_DIR / "mcp-tools.json").read_text())["tools"]
BY_CODE = {c["code"]: c for c in CODES}

# Every package that implements part of the spec, and where it declares the version.
PYTHON_MANIFESTS = ["middleware/pyproject.toml", "providers-service/pyproject.toml",
                    "subscriber-client/sdk-python/pyproject.toml", "mcp/pyproject.toml"]
NODE_MANIFESTS = ["subscriber-client/sdk-js/package.json", "subscriber-client/browser-extension/package.json"]

# MCP servers by spec section (§23.2). Only the operator server may have write tools.
READ_ONLY_SERVERS = {"23.3", "23.4"}
WRITE_SERVER = "23.5"


def _schema(name):
    return json.loads((SCHEMA_DIR / name).read_text())


def _telemetry_fields():
    """Field-code properties of the provider telemetry schema (3 capital letters), with their schema."""
    props = _schema("provider-telemetry.schema.json")["properties"]
    return {k: v for k, v in props.items() if re.fullmatch(r"[A-Z]{3}", k)}


def _units(spec_unit):
    """The spec's unit alternatives: "kg/hr or t/day" -> ["kg/hr", "t/day"]."""
    return [u.strip() for u in spec_unit.split(" or ")]


# --- versions --------------------------------------------------------------------------------


def test_generated_files_agree_on_the_spec_version():
    for name in ("field-codes.json", "mcp-tools.json"):
        assert _schema(name)["spec_version"] == SPEC["spec_version"], name
    assert SPEC["field_codes"] == len(CODES)
    assert SPEC["mcp_tools"] == len(TOOLS)


@pytest.mark.parametrize("path", PYTHON_MANIFESTS)
def test_python_package_declares_the_spec_version(path):
    # Python 3.9 has no tomllib, so read the one key directly.
    m = re.search(r'^\[tool\.stardust\][^\[]*?^spec_version\s*=\s*"([^"]+)"', (REPO / path).read_text(), re.M | re.S)
    assert m, f"{path} needs [tool.stardust] spec_version"
    assert m.group(1) == SPEC["spec_version"]


@pytest.mark.parametrize("path", NODE_MANIFESTS)
def test_node_package_declares_the_spec_version(path):
    manifest = json.loads((REPO / path).read_text())
    assert manifest.get("stardust", {}).get("specVersion") == SPEC["spec_version"]


# --- field codes -----------------------------------------------------------------------------


def test_field_codes_are_unique():
    seen = [c["code"] for c in CODES]
    assert sorted({c for c in seen if seen.count(c) > 1}) == []


def test_provider_telemetry_fields_match_the_spec():
    """Each description reads "<Field Name>[, <unit>][, ...]." and must agree with the spec.

    The unit, when given, must be one of the spec's alternatives ("kg/hr or t/day" accepts either).
    A field the spec types as an enum must be an enum in the schema.
    """
    for code, prop in _telemetry_fields().items():
        assert code in BY_CODE, f"{code} is in provider-telemetry.schema.json but not in the spec"
        spec = BY_CODE[code]
        desc = prop["description"]
        assert desc.startswith(spec["field_name"]), f"{code}: {desc!r} should start with {spec['field_name']!r}"

        rest = desc[len(spec["field_name"]):].rstrip(".")
        if rest.startswith(", ") and not rest[2:].startswith("e.g."):
            stated = rest[2:].split(", ")[0]
            assert any(stated.startswith(u) for u in _units(spec["unit"])), \
                f"{code}: unit {stated!r} isn't one of the spec's {spec['unit']!r}"
        if spec.get("unit") == "enum":
            assert "enum" in prop, f"{code}: the spec types it as an enum"


def test_implemented_enums_match_the_spec():
    tech = set(_schema("provider-telemetry.schema.json")["properties"]["tech_type"]["enum"])
    assert tech == {c["code"] for c in CODES if c["section"] == "20.1"}
    # Every Energy Source Code in the spec's tables is in esc.json. esc.json also has UNK,
    # which the spec defines in its text (§5.3) rather than in a table.
    esc = set(_schema("esc.json")["$defs"]["code"]["enum"])
    assert {c["code"] for c in CODES if c["section"].startswith("5.3")} <= esc


def test_coverage_report():
    """Not a failure: lists the spec's codes that no schema implements yet.

    Counts field codes in the schemas only. Some values Core computes under other names
    (``co2e_g`` is OPE, the ERF resource attribute is ERF) until the codes are adopted.
    """
    implemented = set(_telemetry_fields())
    implemented |= set(_schema("provider-telemetry.schema.json")["properties"]["tech_type"]["enum"])
    implemented |= set(_schema("esc.json")["$defs"]["code"]["enum"])
    missing = [c for c in CODES if c["code"] not in implemented]
    if missing:
        by_section = {}
        for c in missing:
            by_section.setdefault(c["section"], []).append(c["code"])
        lines = "; ".join(f"§{s}: {', '.join(cs)}" for s, cs in sorted(by_section.items(), key=lambda kv: [int(n) for n in kv[0].split(".")]))
        warnings.warn(f"Spec v{SPEC['spec_version']}: {len(implemented & set(BY_CODE))} of {len(CODES)} "
                      f"field codes implemented. Not yet: {lines}", stacklevel=1)


# --- MCP tools -------------------------------------------------------------------------------


def test_mcp_tool_list_is_well_formed():
    names = [t["tool"] for t in TOOLS]
    assert len(names) == len(set(names)), "tool names must be unique across servers"
    assert {t["section"] for t in TOOLS} == READ_ONLY_SERVERS | {WRITE_SERVER}


def test_mcp_servers_match_the_spec():
    """Each MCP server built so far exposes only its spec tools, and only the operator server may
    have a tool that isn't annotated readOnlyHint: true (§23.3–§23.5). Spec tools not built yet are
    reported as a warning, like missing field codes.

    ``stardust_mcp`` needs Python 3.10+, so this runs where the mcp/ package is installed.
    """
    mcp = pytest.importorskip("stardust_mcp", reason="mcp/ package not installed (needs Python 3.10+)")
    expected = {}
    for t in TOOLS:
        expected.setdefault(t["section"], set()).add(t["tool"])
    built = mcp.tool_manifest()
    assert set(built) <= set(expected), "a server for a section the spec doesn't define"
    missing = []
    for section, tools in built.items():
        names = {name for name, _ in tools}
        assert names <= expected[section], f"§{section} has tools the spec doesn't list: {sorted(names - expected[section])}"
        if section in READ_ONLY_SERVERS:
            assert all(read_only for _, read_only in tools), f"§{section} must be read-only"
        missing += [f"§{section}: {n}" for n in sorted(expected[section] - names)]
    missing += [f"§{s}: whole server" for s in sorted(set(expected) - set(built))]
    if missing:
        warnings.warn(f"MCP tools not built yet: {'; '.join(missing)}", stacklevel=1)
