"""The ledger's tables as the API sees them: writable columns, their types, and who may write them.

Column names follow the sheet's tabs (docs/architecture/ledger.md has the column-by-column map).
Values are coerced here, once, for both the API and the importer, so "unknown" stays NULL: an empty
string becomes NULL and a number is never invented.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Mapping, Optional, Tuple

PROTECTED_STATUSES = frozenset({"Reviewed", "Published"})

FIELD_GROUPS = (
    "Status & size", "Power: grid", "Power: sources", "Power: on-site", "Water: supply",
    "Water: use & discharge", "Emissions & air", "Mitigation", "Economics & community",
)
VERDICTS = ("Verified", "Consistent", "Partly verified / missing context", "Unverified", "Contradicted by record")
LINK_STATUSES = ("confirmed", "unconfirmed")

# Scopes (X-Ledger-Key). admin is only honoured under /v1/admin/*, which Caddy never exposes.
OBSERVATIONS_WRITE = "observations:write"
FACILITIES_WRITE = "facilities:write"
TRACKER_WRITE = "tracker:write"
RUNLOG_WRITE = "runlog:write"
ADMIN = "admin"
SCOPES = (OBSERVATIONS_WRITE, FACILITIES_WRITE, TRACKER_WRITE, RUNLOG_WRITE, ADMIN)

FACILITY_ID_RE = re.compile(r"^(DC|PWR)-[A-Z]{2}-[A-Z0-9]{1,3}-[0-9]{4,}$")
STATE_RE = re.compile(r"^[A-Z]{2}-[A-Z0-9]{1,3}$")

PROVENANCE = {
    "source_url": "text", "source_title": "text", "source_type": "text", "source_date": "date",
    "method": "text", "confidence": "confidence", "review_status": "text", "date_found": "date",
}


@dataclass(frozen=True)
class Table:
    name: str
    pk: str
    columns: Dict[str, str]                  # writable column -> type
    scope: str
    prefix: Optional[str] = None             # server-assigned sequence IDs (OBS-0001); None if not
    required: Tuple[str, ...] = ()
    client_pk: bool = False                  # the client names the key (water_ledger: facility_id)
    public: bool = True                      # listed by a public GET endpoint
    extra_read: Tuple[str, ...] = field(default=())  # generated columns returned on reads


FACILITY_COLUMNS = {
    "name": "text", "facility_type": "text", "operator": "text", "tenant": "text", "status": "text",
    "city": "text", "county": "text", "state": "state", "country": "country", "county_fips": "fips",
    "latitude": "latitude", "longitude": "longitude", "location_precision": "text",
    "coordinate_source": "text", "parcel_id": "text", "epa_frs_id": "text", "eia_plant_ids": "text[]",
    "utility": "text", "grid_region": "text", "huc8": "text", "pwsid": "text", "compute_atlas_id": "text",
    "peeringdb_ids": "text[]", "epoch_name": "text", "operational_mw": "number", "planned_mw": "number",
    "epoch_power_mw": "number", "source_confidence": "text", "sources": "text[]",
    "source_record_url": "text", "first_seen": "date", "review_status": "text", "notes": "text",
}

TABLES: Dict[str, Table] = {t.name: t for t in (
    Table("facilities", "facility_id", FACILITY_COLUMNS, FACILITIES_WRITE,
          required=("name", "state", "country")),
    Table("facility_names", "id", {
        "facility_id": "facility_id", "name": "text", "name_type": "text", "source_url": "text",
        "source_title": "text", "review_status": "text"}, FACILITIES_WRITE,
        required=("facility_id", "name"), public=False),
    Table("facility_links", "id", {
        "from_facility_id": "facility_id", "to_facility_id": "facility_id", "relation": "text",
        "link_status": "link_status", "confidence": "confidence", "source_url": "text",
        "source_title": "text", "notes": "text", "review_status": "text"}, FACILITIES_WRITE,
        required=("from_facility_id", "to_facility_id"), public=False),
    Table("observations", "obs_id", {
        "date_found": "date", "facility_label": "text", "operator_tenant": "text", "city_county": "text",
        "state_region": "text", "country": "text", "field_group": "field_group", "field": "text",
        "value": "text", "unit": "text", "source_title": "text", "source_url": "text", "source_type": "text",
        "source_date": "date", "method": "text", "confidence": "confidence", "review_status": "text",
        "notes": "text", "facility_id": "facility_id", "supersedes": "text"}, OBSERVATIONS_WRITE,
        prefix="OBS", required=("field_group", "field", "source_url")),
    Table("claims", "claim_id", {
        "facility_id": "facility_id", "facility_label": "text", "claimant": "text", "claim_text": "text",
        "claim_type": "text", "claim_date": "date", "record_checked": "text", "evidence_supporting": "text",
        "evidence_against": "text", "evidence_observation_ids": "id_list", "verdict": "verdict",
        "reasoning": "text", "operator_response": "text", "next_step": "text", "last_checked": "date",
        **PROVENANCE}, TRACKER_WRITE, prefix="CLM", required=("claim_text",)),
    Table("claim_log", "id", {
        "log_date": "date", "claim_id": "text", "change": "text", "old_verdict": "verdict",
        "new_verdict": "verdict", "evidence_added": "text"}, TRACKER_WRITE,
        required=("log_date", "claim_id", "change"), public=False),
    Table("permit_checks", "check_id", {
        "facility_id": "facility_id", "facility_label": "text", "metric": "text", "limit_value": "number",
        "unit": "text", "limit_type": "text", "limit_source_title": "text", "limit_source_url": "text",
        "actual_value": "number", "actual_as_of": "date", "status": "text", "enforcement_disputes": "text",
        "evidence_observation_ids": "id_list", "next_step": "text", "last_checked": "date", **PROVENANCE},
        TRACKER_WRITE, prefix="PVA", required=("metric",), extra_read=("gap",)),
    Table("water_ledger", "facility_id", {
        "facility_id": "facility_id", "facility_label": "text", "state": "text", "cooling_type": "text",
        "cooling_notes": "text", "supply_source_type": "text", "supplier_name": "text", "pwsid": "text",
        "allocation": "number", "allocation_unit": "text", "allocation_basis": "text",
        "projected_use": "number", "projected_unit": "text", "actual_use": "number", "actual_unit": "text",
        "actual_period": "text", "actual_basis": "text", "withdrawal_vs_consumption": "text",
        "discharge": "text", "reclaimed_water": "text", "drought_terms": "text", "groundwater_wells": "text",
        "basin_water_stress": "text", "key_sources": "text", "evidence_observation_ids": "id_list",
        "next_step": "text", "last_checked": "date", **PROVENANCE}, TRACKER_WRITE,
        required=("facility_id",), client_pk=True, extra_read=("completeness",)),
    Table("who_pays", "item_id", {
        "facility_id": "facility_id", "facility_label": "text", "category": "text", "who_pays": "text",
        "counterparty": "text", "description": "text", "value_usd": "number", "value_basis": "text",
        "term": "text", "conditions": "text", "year": "integer", "evidence_observation_ids": "id_list",
        "next_step": "text", "last_checked": "date", **PROVENANCE}, TRACKER_WRITE, prefix="WP"),
    Table("load_reality", "facility_id", {
        "facility_id": "facility_id", "facility_label": "text", "state": "text", "stated_load_mw": "number",
        "load_basis": "text", "load_source": "text", "operating_load_mw": "number", "operating_basis": "text",
        "status": "text", "land_control": "score", "local_approvals": "score", "power_secured": "score",
        "equipment_financing": "score", "construction": "score", "sponsor_tenant": "score",
        "evidence_for_scores": "text", "red_flags": "text", "sources": "text", "next_step": "text",
        "last_checked": "date", **PROVENANCE}, TRACKER_WRITE, required=("facility_id",), client_pk=True,
        extra_read=("deliverability_score", "tier", "score_weighted_mw")),
    Table("market_context", "id", {
        "measure": "text", "value": "text", "unit": "text", "as_of": "date", "note": "text", **PROVENANCE},
        TRACKER_WRITE, required=("measure",)),
    Table("corrections", "correction_id", {
        "correction_id": "text", "received": "date", "submitter_role": "text", "record_id": "text",
        "facility_id": "text", "issue": "text", "proposed": "text", "evidence_urls": "text[]",
        "proposed_assessment": "text", "decision": "text", "decision_date": "date", "reviewer_note": "text",
        "ledger_change_made": "text", "public_log_id": "text", "review_status": "text"}, ADMIN,
        required=("correction_id",), client_pk=True, public=False),
    Table("source_registry", "source", {
        "source": "text", "what_we_use": "text", "licence": "text", "credit_line": "text", "pulled": "text",
        "records_used": "text", "notes": "text"}, ADMIN, required=("source",), client_pk=True),
    Table("run_log", "id", {
        "run_date": "date", "region": "text", "searches_run": "integer", "new_observations": "integer",
        "changes_flagged": "integer", "notes": "text", "agent": "text"}, RUNLOG_WRITE,
        required=("run_date",), public=False),
)}

ID_LIST_RE = re.compile(r"\b([A-Z]{2,4})-(\d{4,})\b")
RANGE_RE = re.compile(r"\b([A-Z]{2,4})-(\d{4,})\s+to\s+(?:\1-)?(\d{4,})\b")


class ValueProblem(ValueError):
    """A value that can't be stored as given. ``column`` names where."""

    def __init__(self, column: str, message: str):
        super().__init__(f"{column}: {message}")
        self.column = column


def _blank(v: Any) -> bool:
    return v is None or (isinstance(v, str) and v.strip() == "")


def parse_number(v: Any) -> Optional[Decimal]:
    """'$1,900,000,000' -> 1900000000; '' or 'Not quantified' -> None. Raises on anything else."""
    if _blank(v):
        return None
    if isinstance(v, bool):
        raise ValueError("not a number")
    if isinstance(v, (int, float, Decimal)):
        return Decimal(str(v))
    s = str(v).strip()
    if s.lower() in {"not quantified", "unknown", "n/a", "na", "none", "-", "—"}:
        return None
    s = s.replace(",", "").replace("$", "").replace("US", "").strip()
    s = s.lstrip("~≈").strip()
    try:
        d = Decimal(s)
    except InvalidOperation:
        raise ValueError(f"not a number: {v!r}") from None
    if not d.is_finite():
        raise ValueError(f"not a number: {v!r}")
    return d


def parse_date(v: Any) -> Optional[dt.date]:
    if _blank(v):
        return None
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    s = str(v).strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y/%m/%d"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    m = re.match(r"^(\d{4}-\d{2}-\d{2})\b", s)
    if m:
        return dt.date.fromisoformat(m.group(1))
    raise ValueError(f"not a date (YYYY-MM-DD): {v!r}")


def parse_list(v: Any) -> list:
    if _blank(v):
        return []
    if isinstance(v, (list, tuple)):
        return [str(x).strip() for x in v if not _blank(x)]
    return [p.strip() for p in re.split(r"[;,\n]", str(v)) if p.strip()]


def parse_id_list(v: Any) -> list:
    """'OBS-0017; OBS-0034' or 'OBS-0028 to OBS-0031' -> a list of IDs, ranges expanded."""
    if isinstance(v, (list, tuple)):
        v = "; ".join(str(x) for x in v)
    if _blank(v):
        return []
    s, out = str(v), []
    for m in RANGE_RE.finditer(s):
        prefix, lo, hi = m.group(1), int(m.group(2)), int(m.group(3))
        width = len(m.group(2))
        if 0 <= hi - lo <= 500:
            out.extend(f"{prefix}-{n:0{width}d}" for n in range(lo, hi + 1))
    s = RANGE_RE.sub(" ", s)
    out.extend(f"{p}-{n}" for p, n in ID_LIST_RE.findall(s))
    seen, uniq = set(), []
    for x in out:
        if x not in seen:
            seen.add(x)
            uniq.append(x)
    return uniq


def coerce(column: str, kind: str, v: Any) -> Any:
    """One value to its stored form. Raises ValueProblem when it can't be stored as given."""
    try:
        if kind in ("text[]",):
            return parse_list(v)
        if kind == "id_list":
            return parse_id_list(v)
        if _blank(v):
            return None
        if kind == "text":
            return str(v).strip()
        if kind == "number":
            return parse_number(v)
        if kind == "integer":
            d = parse_number(v)
            if d is None:
                return None
            if d != d.to_integral_value():
                raise ValueError(f"not a whole number: {v!r}")
            return int(d)
        if kind == "score":
            d = parse_number(v)
            if d is None:
                return None
            if d not in (0, 1, 2):
                raise ValueError(f"scores are 0, 1 or 2, got {v!r}")
            return int(d)
        if kind == "confidence":
            d = parse_number(v)
            if d is None:
                return None
            if not (0 <= d <= 1):
                raise ValueError(f"confidence must be between 0 and 1, got {v!r}")
            return d
        if kind == "date":
            return parse_date(v)
        if kind in ("latitude", "longitude"):
            d = parse_number(v)
            if d is None:
                return None
            lim = 90 if kind == "latitude" else 180
            if not (-lim <= d <= lim):
                raise ValueError(f"{kind} out of range: {v!r}")
            return float(d)
        if kind == "state":
            s = str(v).strip().upper()
            if not STATE_RE.match(s):
                raise ValueError(f"state must be ISO 3166-2 (e.g. US-TX), got {v!r}")
            return s
        if kind == "country":
            s = str(v).strip().upper()
            if not re.fullmatch(r"[A-Z]{2}", s):
                raise ValueError(f"country must be ISO 3166-1 alpha-2 (e.g. US), got {v!r}")
            return s
        if kind == "fips":
            s = str(v).strip()
            if s.isdigit() and len(s) < 5:
                s = s.zfill(5)
            if not re.fullmatch(r"\d{5}", s):
                raise ValueError(f"county FIPS must be 5 digits, got {v!r}")
            return s
        if kind == "facility_id":
            s = str(v).strip().upper()
            if not FACILITY_ID_RE.match(s):
                raise ValueError(f"not a facility ID (e.g. DC-US-TX-0152): {v!r}")
            return s
        if kind == "field_group":
            s = str(v).strip()
            if s not in FIELD_GROUPS:
                raise ValueError(f"field_group must be one of: {'; '.join(FIELD_GROUPS)}")
            return s
        if kind == "verdict":
            s = str(v).strip()
            if s not in VERDICTS:
                raise ValueError(f"verdict must be one of: {'; '.join(VERDICTS)}")
            return s
        if kind == "link_status":
            s = str(v).strip().lower()
            if s not in LINK_STATUSES:
                raise ValueError("link_status must be 'confirmed' or 'unconfirmed'")
            return s
    except ValueProblem:
        raise
    except ValueError as e:
        raise ValueProblem(column, str(e)) from None
    raise ValueProblem(column, f"unknown column type {kind}")


def clean(table: Table, data: Mapping[str, Any], *, partial: bool = False,
          allow: Tuple[str, ...] = ()) -> Dict[str, Any]:
    """Validate a write body against a table: unknown columns and bad values raise ValueProblem.

    ``partial`` is for PATCH (required columns may be absent). ``allow`` lets extra keys through
    untouched (idempotency_key, id_prefix).
    """
    out: Dict[str, Any] = {}
    for k, v in data.items():
        if k in allow:
            out[k] = v
            continue
        kind = table.columns.get(k)
        if kind is None:
            raise ValueProblem(k, f"unknown field for {table.name}")
        out[k] = coerce(k, kind, v)
    if not partial:
        for r in table.required:
            if _blank(out.get(r)) and out.get(r) != []:
                raise ValueProblem(r, "required")
    return out
