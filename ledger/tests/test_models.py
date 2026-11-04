"""Value coercion: no database needed."""

import datetime as dt
from decimal import Decimal

import pytest

from ledger import auth
from ledger.models import TABLES, ValueProblem, clean, coerce, parse_id_list, parse_number


def test_numbers_keep_unknown_as_none():
    assert parse_number("$1,900,000,000") == Decimal("1900000000")
    assert parse_number("~5.5") == Decimal("5.5")
    assert parse_number("") is None
    assert parse_number("Not quantified") is None
    with pytest.raises(ValueError):
        parse_number("about five")


def test_id_lists_expand_ranges():
    assert parse_id_list("OBS-0017; OBS-0034") == ["OBS-0017", "OBS-0034"]
    assert parse_id_list("OBS-0028 to OBS-0031") == ["OBS-0028", "OBS-0029", "OBS-0030", "OBS-0031"]
    assert parse_id_list("") == []


@pytest.mark.parametrize("kind,value", [
    ("confidence", "1.5"), ("score", "3"), ("field_group", "Power"), ("verdict", "True"),
    ("state", "Texas"), ("facility_id", "DC-TX-1"), ("fips", "4845"[:3] + "x"), ("date", "yesterday"),
])
def test_bad_values_are_refused(kind, value):
    with pytest.raises(ValueProblem):
        coerce("col", kind, value)


def test_coercions():
    assert coerce("c", "date", "2026-10-29") == dt.date(2026, 10, 29)
    assert coerce("c", "fips", "8013") == "08013"
    assert coerce("c", "state", "us-tx") == "US-TX"
    assert coerce("c", "text[]", "a; b, c") == ["a", "b", "c"]
    assert coerce("c", "confidence", "0.75") == Decimal("0.75")


def test_clean_rejects_unknown_and_missing_fields():
    with pytest.raises(ValueProblem) as e:
        clean(TABLES["observations"], {"field_group": "Mitigation", "field": "x", "source_url": "u", "colour": 1})
    assert e.value.column == "colour"
    with pytest.raises(ValueProblem) as e:
        clean(TABLES["observations"], {"field_group": "Mitigation", "field": "x"})
    assert e.value.column == "source_url"


def test_keys_are_hashed_and_split():
    key_id, key, h = auth.new_key()
    assert key.startswith("lk_") and auth.split_key(key) == key_id
    assert auth.matches(key, h) and not auth.matches(key + "x", h)
    assert auth.split_key("sdk_abc_def") is None
