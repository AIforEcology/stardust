"""The JSON Schemas in /schema are the published standard; Core's output must conform to them."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

jsonschema = pytest.importorskip("jsonschema")
from referencing import Registry, Resource  # noqa: E402

from stardust_core.models import UsageEvent  # noqa: E402

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schema"
BASE = "https://github.com/AIforEcology/stardust/schema/"


def _schema_files():
    # Skip macOS "._" resource forks that exFAT volumes create.
    return [p for p in SCHEMA_DIR.glob("*.json") if not p.name.startswith("._")]


def _validator(name):
    resources = []
    for p in _schema_files():
        resources.append((BASE + p.name, Resource.from_contents(json.loads(p.read_text()))))
    registry = Registry().with_resources(resources)
    schema = json.loads((SCHEMA_DIR / name).read_text())
    return jsonschema.Draft202012Validator(schema, registry=registry, format_checker=jsonschema.FormatChecker())


def _event(**kw):
    base = dict(source_layer="browser_ext", provider="openai", model="gpt-4o",
                timestamp=datetime(2026, 9, 23, tzinfo=timezone.utc), tokens_in=1200, tokens_out=300)
    base.update(kw)
    return UsageEvent(**base)


def test_enriched_event_matches_schema(engine):
    out = json.loads(engine.enrich(_event(region="us-east-1")).model_dump_json())
    _validator("enriched-event.schema.json").validate(out)


def test_enriched_event_without_tokens_matches_schema(engine):
    out = json.loads(engine.enrich(_event(tokens_in=None, tokens_out=None, model="unknown")).model_dump_json())
    _validator("enriched-event.schema.json").validate(out)


def test_usage_event_schema_rejects_unknown_fields():
    v = _validator("usage-event.schema.json")
    ok = {"source_layer": "browser_ext", "provider": "openai", "model": "gpt-4o", "timestamp": "2026-09-23T00:00:00Z"}
    v.validate(ok)
    with pytest.raises(jsonschema.ValidationError):
        v.validate({**ok, "prompt_text": "should never be sent"})


def test_enriched_schema_rejects_unknown_fields(engine):
    out = json.loads(engine.enrich(_event()).model_dump_json())
    with pytest.raises(jsonschema.ValidationError):
        _validator("enriched-event.schema.json").validate({**out, "surprise": 1})


def test_all_schemas_are_valid_json_schema():
    for p in _schema_files():
        jsonschema.Draft202012Validator.check_schema(json.loads(p.read_text()))
