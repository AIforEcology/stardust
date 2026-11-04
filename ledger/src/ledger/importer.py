"""One-time import of the Google Sheet's tabs (CSV exports) into the ledger.

Two halves:

- ``prepare(folder)`` reads the CSVs, maps each tab's columns to the ledger's, validates every value
  and returns load-ready batches plus a report. It never talks to the database.
- ``load(conn, batch)`` upserts one batch by ID inside an admin transaction, one savepoint per row,
  so a bad row is reported instead of stopping the import. Re-running loads nothing twice.

The admin import endpoint (POST /v1/admin/import) and scripts/import_sheet.py --dsn both call load().
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import psycopg
from psycopg import sql

from . import store
from .models import FACILITY_ID_RE, TABLES, ValueProblem, coerce

# --- sheet columns -> ledger columns --------------------------------------------------------------
# Keys are the sheet's header text exactly as exported (compared ignoring case, spaces and dash style).
# Columns starting with "_" are handled by the importer itself; None means "computed by the database".

FACILITIES_TAB = {
    "Facility ID": "facility_id", "Name": "name", "Other names": "_other_names", "Facility type": "facility_type",
    "Operator / developer": "operator", "Tenant": "tenant", "Latest status": "status", "City": "city",
    "County": "county", "State (ISO 3166-2)": "state", "Country (ISO)": "country", "County FIPS": "county_fips",
    "Latitude": "latitude", "Longitude": "longitude", "Location precision": "location_precision",
    "Parcel ID": "parcel_id", "EPA FRS ID": "epa_frs_id", "EIA plant ID(s)": "eia_plant_ids", "Utility": "utility",
    "Grid region": "grid_region", "Watershed (HUC-8)": "huc8", "Water supplier (PWSID)": "pwsid",
    "Serves / served by": "_serves", "First seen": "first_seen", "Review status": "review_status",
    "Notes": "notes", "Coordinate source": "coordinate_source",
}
MASTER_TAB = {
    "Facility ID": "facility_id", "Name": "name", "Operator": "operator", "Type": "facility_type",
    "Status": "status", "City": "city", "County": "county", "State": "_state", "Latitude": "latitude",
    "Longitude": "longitude", "Location precision": "location_precision", "Operational MW": "operational_mw",
    "Planned MW": "planned_mw", "Epoch AI power MW": "epoch_power_mw", "Offtaker / tenant": "tenant",
    "Confidence (source)": "source_confidence", "Sources": "sources", "Compute Atlas ID": "compute_atlas_id",
    "PeeringDB IDs": "peeringdb_ids", "Epoch AI name": "epoch_name", "Source record URL": "source_record_url",
    "Curated Facilities ID": "_curated", "Review status": "review_status", "Notes": "notes",
}
OBSERVATIONS_TAB = {
    "ID": "obs_id", "Date found": "date_found", "Facility": "facility_label", "Operator / tenant": "operator_tenant",
    "City / county": "city_county", "State / region": "state_region", "Country": "country",
    "Field group": "field_group", "Field": "field", "Value": "value", "Unit": "unit", "Source title": "source_title",
    "Source URL": "source_url", "Source type": "source_type", "Source date": "source_date", "Method": "method",
    "Confidence (0-1)": "confidence", "Review status": "review_status", "Notes": "notes",
    "Facility ID": "facility_id", "Supersedes": "supersedes",
}
CLAIMS_TAB = {
    "Claim ID": "claim_id", "Facility ID": "facility_id", "Facility": "facility_label", "Claimant": "claimant",
    "Claim (exact words)": "claim_text", "Claim type": "claim_type", "Claim date": "claim_date",
    "Claim source title": "source_title", "Claim source URL": "source_url", "Record checked": "record_checked",
    "Evidence supporting": "evidence_supporting", "Evidence against or complicating": "evidence_against",
    "Evidence observation IDs": "evidence_observation_ids", "Verdict": "verdict", "Reasoning": "reasoning",
    "Confidence (0-1)": "confidence", "Operator response": "operator_response", "Next step": "next_step",
    "Last checked": "last_checked", "Review status": "review_status",
}
CLAIM_LOG_TAB = {
    "Date": "log_date", "Claim ID": "claim_id", "Change": "change", "Old verdict": "old_verdict",
    "New verdict": "new_verdict", "Evidence added": "evidence_added",
}
WATER_TAB = {
    "Facility ID": "facility_id", "Facility": "facility_label", "State": "state", "Cooling type": "cooling_type",
    "Cooling notes": "cooling_notes", "Supply source type": "supply_source_type",
    "Supplier / source name": "supplier_name", "Water system ID (PWSID)": "pwsid",
    "Allocation or cap": "allocation", "Allocation unit": "allocation_unit", "Allocation basis": "allocation_basis",
    "Projected or design use": "projected_use", "Projected unit": "projected_unit",
    "Reported actual use": "actual_use", "Actual unit": "actual_unit", "Actual period": "actual_period",
    "Actual basis": "actual_basis", "Withdrawal vs consumption": "withdrawal_vs_consumption",
    "Discharge / wastewater": "discharge", "Reclaimed or alternative water": "reclaimed_water",
    "Drought / curtailment terms": "drought_terms", "Groundwater wells": "groundwater_wells",
    "Basin water stress (WRI Aqueduct)": "basin_water_stress", "Completeness (of 6)": None,
    "Key sources": "key_sources", "Evidence observation IDs": "evidence_observation_ids",
    "Review status": "review_status", "Next step": "next_step", "Last checked": "last_checked",
}
WHO_PAYS_TAB = {
    "Item ID": "item_id", "Facility ID": "facility_id", "Facility": "facility_label", "Category": "category",
    "Who pays": "who_pays", "Granting body or counterparty": "counterparty", "Description": "description",
    "Value (USD)": "value_usd", "Value basis": "value_basis", "Term": "term",
    "Conditions, clawbacks or caveats": "conditions", "Year": "year", "Source title": "source_title",
    "Source URL": "source_url", "Evidence observation IDs": "evidence_observation_ids",
    "Confidence (0-1)": "confidence", "Next step": "next_step", "Last checked": "last_checked",
    "Review status": "review_status",
}
LOAD_TAB = {
    "Facility ID": "facility_id", "Facility": "facility_label", "State": "state",
    "Stated load (MW)": "stated_load_mw", "Load basis": "load_basis", "Load source": "load_source",
    "Operating load (MW)": "operating_load_mw", "Operating basis": "operating_basis", "Status": "status",
    "Land control (0-2)": "land_control", "Local approvals (0-2)": "local_approvals",
    "Power secured (0-2)": "power_secured", "Equipment & financing (0-2)": "equipment_financing",
    "Construction (0-2)": "construction", "Sponsor & tenant (0-2)": "sponsor_tenant",
    "Deliverability score (0-100)": None, "Tier": None,
    "Score-weighted MW (illustrative, not a forecast)": None, "Evidence for scores": "evidence_for_scores",
    "Red flags": "red_flags", "Sources": "sources", "Next step": "next_step", "Last checked": "last_checked",
    "Review status": "review_status",
}
MARKET_BLOCK = {"Measure": "measure", "Value": "value", "Unit": "unit", "As of": "as_of", "Source": "source_url",
                "Note": "note"}
PERMIT_TAB = {
    "Check ID": "check_id", "Facility ID": "facility_id", "Facility": "facility_label", "Metric": "metric",
    "Limit": "limit_value", "Unit": "unit", "Limit type": "limit_type", "Limit source title": "limit_source_title",
    "Limit source URL": "limit_source_url", "Actual value": "actual_value", "Actual as of": "actual_as_of",
    "Actual source title": "source_title", "Actual source URL": "source_url", "Actual method": "method",
    "Gap (actual − limit)": None, "Status": "status", "Enforcement / disputes": "enforcement_disputes",
    "Evidence observation IDs": "evidence_observation_ids", "Next step": "next_step",
    "Last checked": "last_checked", "Review status": "review_status",
}
RUN_LOG_TAB = {
    "Run date": "run_date", "Region": "region", "Searches run": "searches_run",
    "New observations": "new_observations", "Changes flagged": "changes_flagged", "Notes / errors": "notes",
}
CORRECTIONS_TAB = {
    "Correction ID": "correction_id", "Received": "received", "Submitter role": "submitter_role",
    "Record ID": "record_id", "Facility ID": "facility_id", "Issue": "issue", "Proposed": "proposed",
    "Evidence URLs": "evidence_urls", "Proposed assessment (agent)": "proposed_assessment",
    "Decision": "decision", "Decision date": "decision_date", "Reviewer note": "reviewer_note",
    "Ledger change made": "ledger_change_made", "Public log ID": "public_log_id",
}
SOURCES_TAB = {
    "Source": "source", "What we use": "what_we_use", "Licence / terms": "licence",
    "Required credit line": "credit_line", "Pulled": "pulled", "Records used": "records_used", "Notes": "notes",
}

# (tab name, ledger table, column map, key column). Order matters: referenced rows load first.
TABS = (
    ("Sources & credits", "source_registry", SOURCES_TAB, "source"),
    ("Facilities", "facilities", FACILITIES_TAB, "facility_id"),
    ("US Master List", "facilities", MASTER_TAB, "facility_id"),
    ("Observations", "observations", OBSERVATIONS_TAB, "obs_id"),
    ("Claims", "claims", CLAIMS_TAB, "claim_id"),
    ("Claim log", "claim_log", CLAIM_LOG_TAB, None),
    ("Permitted vs actual", "permit_checks", PERMIT_TAB, "check_id"),
    ("Water ledger", "water_ledger", WATER_TAB, "facility_id"),
    ("Who pays", "who_pays", WHO_PAYS_TAB, "item_id"),
    ("Load reality", "load_reality", LOAD_TAB, "facility_id"),
    ("Corrections", "corrections", CORRECTIONS_TAB, "correction_id"),
    ("Run log", "run_log", RUN_LOG_TAB, None),
)

ID_PATTERNS = {"obs_id": r"^OBS-\d{4,}$", "claim_id": r"^CLM-\d{4,}$", "item_id": r"^WP-\d{4,}$",
               "check_id": r"^PVA-\d{4,}$"}

# Rough boxes for US states and territories (lat_min, lat_max, lon_min, lon_max).
US_BOXES = (
    (24.3, 49.5, -125.0, -66.8),     # contiguous US
    (51.0, 71.6, -180.0, -129.9),    # Alaska
    (51.0, 53.1, 172.0, 180.0),      # Aleutians west of 180
    (18.8, 22.4, -160.4, -154.7),    # Hawaii
    (17.6, 18.6, -67.5, -64.5),      # Puerto Rico, USVI
    (13.2, 20.6, 144.5, 146.1),      # Guam, Northern Marianas
    (-14.6, -14.1, -171.1, -169.4),  # American Samoa
)


def in_us(lat: float, lon: float) -> bool:
    return any(a <= lat <= b and c <= lon <= d for a, b, c, d in US_BOXES)


def _norm(h: str) -> str:
    return re.sub(r"\s+", " ", h.replace("−", "-").replace("–", "-").replace("—", "-")).strip().lower()


@dataclass
class Batch:
    table: str
    mode: str                       # "upsert": sheet values win; "fill": only fill blank fields
    rows: List[Dict[str, Any]]
    tab: str
    row_numbers: List[int] = field(default_factory=list)  # sheet row number of each row, for the report


@dataclass
class Report:
    loaded: Dict[str, int] = field(default_factory=dict)
    unchanged: Dict[str, int] = field(default_factory=dict)
    rejected: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[Dict[str, Any]] = field(default_factory=list)
    duplicate_ids: List[Dict[str, Any]] = field(default_factory=list)
    outside_us: List[Dict[str, Any]] = field(default_factory=list)
    bad_confidence: List[Dict[str, Any]] = field(default_factory=list)
    dropped_columns: Dict[str, List[str]] = field(default_factory=dict)
    missing_tabs: List[str] = field(default_factory=list)

    def reject(self, tab: str, row: int, reason: str, rid: Optional[str] = None) -> None:
        self.rejected.append({"tab": tab, "row": row, "id": rid, "reason": reason})

    def warn(self, tab: str, row: int, reason: str, rid: Optional[str] = None) -> None:
        self.warnings.append({"tab": tab, "row": row, "id": rid, "reason": reason})

    def to_dict(self) -> Dict[str, Any]:
        return self.__dict__

    def markdown(self) -> str:
        lines = ["# Ledger import report", "", "| Table | Loaded or updated | Unchanged |", "|---|---|---|"]
        for t in sorted(set(self.loaded) | set(self.unchanged)):
            lines.append(f"| {t} | {self.loaded.get(t, 0)} | {self.unchanged.get(t, 0)} |")
        for title, items in (("Rejected rows", self.rejected), ("Duplicate IDs", self.duplicate_ids),
                             ("Coordinates outside the US", self.outside_us),
                             ("Bad confidence values (left blank)", self.bad_confidence),
                             ("Other warnings", self.warnings)):
            lines += ["", f"## {title} ({len(items)})", ""]
            lines += [f"- {i.get('tab')} row {i.get('row')} {i.get('id') or ''}: {i.get('reason')}" for i in items[:2000]]
        if self.dropped_columns:
            lines += ["", "## Columns not imported", ""]
            lines += [f"- {tab}: {', '.join(cols)}" for tab, cols in self.dropped_columns.items()]
        if self.missing_tabs:
            lines += ["", "## Tabs with no CSV file", "", *[f"- {t}" for t in self.missing_tabs]]
        return "\n".join(lines) + "\n"


# --- reading the CSVs -----------------------------------------------------------------------------

def find_csv(folder: Path, tab: str) -> Optional[Path]:
    """Google Sheets names a tab export '<spreadsheet> - <tab>.csv'; a bare '<tab>.csv' works too."""
    want = _norm(tab)
    for p in sorted(folder.glob("*.csv")):
        stem = _norm(p.stem)
        if stem == want or stem.endswith(" - " + want):
            return p
    return None


def read_rows(path: Path) -> List[List[str]]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        return [row for row in csv.reader(f)]


def _map_header(header: List[str], mapping: Dict[str, Optional[str]], tab: str, report: Report) -> List[Optional[str]]:
    lookup = {_norm(k): v for k, v in mapping.items()}
    out, dropped = [], []
    for h in header:
        n = _norm(h)
        if n in lookup:
            out.append(lookup[n])
        else:
            out.append(None)
            if n:
                dropped.append(h.strip())
    if dropped:
        report.dropped_columns[tab] = dropped  # names only; values in unknown columns are never read
    return out


def _coerce_row(table: str, raw: Dict[str, str], tab: str, rownum: int, key: Optional[str],
                report: Report) -> Optional[Dict[str, Any]]:
    """Coerce a mapped row. A bad value in an optional column is left blank and reported; a bad key
    or required value rejects the row."""
    t = TABLES[table]
    rid = raw.get(key) if key else None
    out: Dict[str, Any] = {}
    for col, val in raw.items():
        if col.startswith("_"):
            continue
        kind = t.columns.get(col)
        if kind is None:
            kind = "facility_id" if col == "facility_id" else "text"
        try:
            out[col] = coerce(col, kind, val)
        except ValueProblem as e:
            if col == key or col in t.required:
                report.reject(tab, rownum, str(e), rid)
                return None
            if kind == "confidence":
                report.bad_confidence.append({"tab": tab, "row": rownum, "id": rid, "reason": str(e)})
            else:
                report.warn(tab, rownum, f"{e} (left blank)", rid)
            out[col] = None if kind not in ("text[]", "id_list") else []
    for r in t.required:
        if out.get(r) in (None, "", []):
            report.reject(tab, rownum, f"{r}: required", rid)
            return None
    return out


def prepare(folder: Path) -> Tuple[List[Batch], Report]:
    report = Report()
    batches: List[Batch] = []
    curated_ids: Dict[str, int] = {}
    facility_ids: set = set()
    names_rows, names_nums, links_rows, links_nums, alias_rows, alias_nums = [], [], [], [], [], []

    for tab, table, mapping, key in TABS:
        path = find_csv(folder, tab)
        if path is None:
            report.missing_tabs.append(tab)
            continue
        rows = read_rows(path)
        if not rows:
            continue
        header = _map_header(rows[0], mapping, tab, report)
        batch = Batch(table, "fill" if tab == "US Master List" else "upsert", [], tab)
        market = Batch("market_context", "upsert", [], tab) if tab == "Load reality" else None
        seen: Dict[str, int] = {}
        in_market, market_header = False, None
        for i, cells in enumerate(rows[1:], start=2):
            if not any(c.strip() for c in cells):
                continue
            if market is not None and cells and _norm(cells[0]) == "market context":
                in_market = True
                continue
            if in_market:
                if market_header is None:
                    market_header = _map_header(cells, MARKET_BLOCK, tab + " (market context)", report)
                    continue
                raw = {c: v for c, v in zip(market_header, cells) if c}
                row = _coerce_row("market_context", raw, tab, i, None, report)
                if row:
                    row["idempotency_key"] = "import:" + _digest(["market_context", row.get("measure"), raw.get("as_of")])
                    market.rows.append(row)
                    market.row_numbers.append(i)
                continue

            raw = {c: v for c, v in zip(header, cells) if c}
            rid = (raw.get(key) or "").strip().upper() if key else None
            if key:
                if not rid:
                    report.reject(tab, i, f"no {key}")
                    continue
                if key == "facility_id" and table == "facilities" and not FACILITY_ID_RE.match(rid):
                    report.reject(tab, i, "not a facility ID (e.g. DC-US-TX-0152)", rid)
                    continue
                if key in ID_PATTERNS and not re.match(ID_PATTERNS[key], rid):
                    report.reject(tab, i, f"{key} has the wrong format", rid)
                    continue
                if rid in seen:
                    report.duplicate_ids.append({"tab": tab, "row": i, "id": rid,
                                                 "reason": f"same ID as row {seen[rid]}; kept the first"})
                    continue
                seen[rid] = i
                raw[key] = rid

            if table == "facilities":
                row = _facility_row(tab, raw, i, report, curated_ids, facility_ids, alias_rows, alias_nums)
                if row is None:
                    continue
                if tab == "Facilities":
                    for name in [n.strip() for n in re.split(r";", raw.get("_other_names") or "") if n.strip()]:
                        names_rows.append({"facility_id": rid, "name": name, "name_type": "Other name",
                                           "source_url": row.get("coordinate_source")})
                        names_nums.append(i)
                    for link in _links(rid, raw.get("_serves") or ""):
                        links_rows.append(link)
                        links_nums.append(i)
            else:
                row = _coerce_row(table, raw, tab, i, key, report)
                if row is None:
                    continue
                if key is None:
                    row["idempotency_key"] = "import:" + _digest([table] + [str(cells)])
            batch.rows.append(row)
            batch.row_numbers.append(i)
        if table == "observations":
            order = sorted(range(len(batch.rows)), key=lambda k: int(batch.rows[k]["obs_id"].split("-")[1]))
            batch.rows = [batch.rows[k] for k in order]
            batch.row_numbers = [batch.row_numbers[k] for k in order]
        batches.append(batch)
        if tab == "US Master List":
            batches.append(Batch("facility_aliases", "upsert", alias_rows, tab, alias_nums))
        if tab == "Facilities":
            batches.append(Batch("facility_names", "upsert", names_rows, tab, names_nums))
            batches.append(Batch("facility_links", "upsert", links_rows, tab, links_nums))
        if market is not None:
            batches.append(market)
    # Facilities from both tabs first, then aliases, names and links (links may point at Master List sites).
    batches.sort(key=lambda b: _ORDER.get(b.table, 50))
    return batches, report


_ORDER = {"source_registry": 0, "facilities": 1, "facility_aliases": 2, "facility_names": 3, "facility_links": 4,
          "observations": 5, "claims": 6, "claim_log": 7}


def _digest(parts: Iterable[Any]) -> str:
    return hashlib.sha256(json.dumps([str(p) for p in parts]).encode()).hexdigest()[:32]


def _facility_row(tab: str, raw: Dict[str, str], i: int, report: Report, curated_ids: Dict[str, int],
                  facility_ids: set, alias_rows: list, alias_nums: list) -> Optional[Dict[str, Any]]:
    rid = raw["facility_id"]
    _, country, sub, _ = rid.split("-", 3)
    if tab == "US Master List":
        st = (raw.pop("_state", "") or "").strip().upper()
        raw["state"] = f"US-{st}" if re.fullmatch(r"[A-Z]{2}", st) else (st or None)
        raw["country"] = "US"
        curated = (raw.get("_curated") or "").strip().upper()
        if curated and curated != rid:
            if curated not in curated_ids:
                report.reject(tab, i, f"Curated Facilities ID {curated} is not on the Facilities tab", rid)
                return None
            if rid in facility_ids:
                report.duplicate_ids.append({"tab": tab, "row": i, "id": rid,
                                             "reason": "this Master List ID is already used by another facility"})
                return None
            alias_rows.append({"alias_id": rid, "facility_id": curated, "source": "US Master List"})
            alias_nums.append(i)
            raw["facility_id"] = curated  # fill the curated facility's blank fields from this row
        elif not curated and rid in curated_ids:
            report.duplicate_ids.append({"tab": tab, "row": i, "id": rid, "reason":
                                         f"ID already used on the Facilities tab (row {curated_ids[rid]}) "
                                         "and this row is not linked to it"})
            return None
    if not raw.get("state"):
        raw["state"] = f"{country}-{sub}"
        report.warn(tab, i, "no state; taken from the facility ID", rid)
    if not raw.get("country"):
        raw["country"] = country
    row = _coerce_row("facilities", raw, tab, i, "facility_id", report)
    if row is None:
        return None
    if row.get("state") and row["state"] != f"{country}-{sub}" and tab == "Facilities":
        report.warn(tab, i, f"state {row['state']} differs from the facility ID", rid)
    lat, lon = row.get("latitude"), row.get("longitude")
    if (lat is None) != (lon is None):
        report.warn(tab, i, "only one of latitude/longitude given (both left blank)", rid)
        row["latitude"] = row["longitude"] = None
    elif lat is not None and row.get("country") == "US" and not in_us(lat, lon):
        report.outside_us.append({"tab": tab, "row": i, "id": rid, "reason": f"{lat}, {lon} is outside the US"})
    if tab == "Facilities":
        curated_ids[rid] = i
    facility_ids.add(rid)
    return row


LINK_ID_RE = re.compile(r"\b(?:DC|PWR)-[A-Z]{2}-[A-Z0-9]{1,3}-\d{4,}\b")


def _links(fid: str, text: str) -> List[Dict[str, Any]]:
    """'Serves / served by' is free text naming other facility IDs. Phrases such as 'served by X'
    point the link at this facility; anything else means this facility serves X."""
    out = []
    for part in re.split(r"[;\n]", text):
        lower = part.lower()
        status = "confirmed" if re.search(r"\bconfirmed\b", lower) and "unconfirmed" not in lower else "unconfirmed"
        for other in LINK_ID_RE.findall(part.upper()):
            if other == fid:
                continue
            served_by = "served by" in lower or "powered by" in lower
            out.append({"from_facility_id": other if served_by else fid, "to_facility_id": fid if served_by else other,
                        "relation": "serves", "link_status": status, "notes": part.strip()[:500]})
    return out


# --- loading --------------------------------------------------------------------------------------

def load(conn: psycopg.Connection, table: str, mode: str, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Upsert rows by ID in the caller's (admin) transaction. Returns one result per row:
    {"status": "inserted" | "updated" | "unchanged" | "rejected", "id": ..., "reason": ...}."""
    results = []
    for row in rows:
        try:
            with conn.transaction():  # savepoint: one bad row doesn't undo the rest
                results.append(_load_one(conn, table, mode, dict(row)))
        except (psycopg.Error, ValueProblem, store.Conflict) as e:
            msg = getattr(getattr(e, "diag", None), "message_primary", None) or str(e)
            results.append({"status": "rejected", "id": _row_id(table, row), "reason": msg})
    return results


def _row_id(table: str, row: Dict[str, Any]) -> Optional[str]:
    if table == "facility_aliases":
        return row.get("alias_id")
    t = TABLES.get(table)
    return str(row.get(t.pk)) if t and row.get(t.pk) is not None else None


def _load_one(conn: psycopg.Connection, table: str, mode: str, row: Dict[str, Any]) -> Dict[str, Any]:
    if table == "facility_aliases":
        r = conn.execute("""INSERT INTO facility_aliases (alias_id, facility_id, source) VALUES (%s, %s, %s)
                            ON CONFLICT (alias_id) DO NOTHING RETURNING alias_id""",
                         (coerce("alias_id", "facility_id", row["alias_id"]),
                          coerce("facility_id", "facility_id", row["facility_id"]), row.get("source") or "import")).fetchone()
        if r is None:
            existing = conn.execute("SELECT facility_id FROM facility_aliases WHERE alias_id = %s",
                                    (row["alias_id"],)).fetchone()
            if existing["facility_id"] != row["facility_id"]:
                raise ValueProblem("alias_id", f"already an alias of {existing['facility_id']}")
        return {"status": "inserted" if r else "unchanged", "id": row["alias_id"]}

    t = TABLES[table]
    idem = row.pop("idempotency_key", None)
    pk_value = row.pop(t.pk, None) if not t.client_pk and t.pk != "id" else None
    data = {k: coerce(k, t.columns[k], v) for k, v in row.items() if k in t.columns}
    unknown = set(row) - set(t.columns)
    if unknown:
        raise ValueProblem(sorted(unknown)[0], f"unknown field for {table}")

    if table in ("facility_names", "facility_links") or (t.pk == "id" and idem):
        _, replayed = store.create(conn, table, {**data, **({"idempotency_key": idem} if idem else {})}, admin=True)
        return {"status": "unchanged" if replayed else "inserted", "id": None}

    if t.client_pk:
        pk_value = data[t.pk]
    if pk_value is None:
        raise ValueProblem(t.pk, "required")
    pk_value = coerce(t.pk, "facility_id", pk_value) if t.pk == "facility_id" else str(pk_value).strip()
    existing = store._raw(conn, t, pk_value, for_update=True)
    if existing is None:
        for r in t.required:
            if data.get(r) in (None, "", []):
                raise ValueProblem(r, "required")
        store._insert(conn, t, {t.pk: pk_value, **data, **({"idempotency_key": idem} if idem else {})})
        return {"status": "inserted", "id": pk_value}

    if table == "observations":
        changes = {k: data[k] for k in ("review_status", "notes")
                   if k in data and not store._same(existing.get(k), data[k])}
    else:
        changes = {}
        for k, v in data.items():
            if k == t.pk:
                continue
            old = existing.get(k)
            if store._same(old, v) or (store._empty(old) and store._empty(v)):
                continue
            if store._empty(v):
                continue  # a blank cell never erases a value (unknown is not "remove")
            if mode == "fill" and not store._empty(old):
                continue
            changes[k] = v
        if table == "facilities" and ("latitude" in changes or "longitude" in changes):
            changes.setdefault("latitude", existing.get("latitude"))
            changes.setdefault("longitude", existing.get("longitude"))
            if changes["latitude"] is None or changes["longitude"] is None:
                changes.pop("latitude"), changes.pop("longitude")
    if not changes:
        return {"status": "unchanged", "id": pk_value}
    store._update(conn, t, pk_value, changes)
    return {"status": "updated", "id": pk_value}


def record(report: Report, batch: Batch, results: List[Dict[str, Any]]) -> None:
    for num, res in zip(batch.row_numbers or [None] * len(results), results):
        if res["status"] == "rejected":
            report.reject(batch.tab, num, f"{batch.table}: {res['reason']}", res.get("id"))
        elif res["status"] == "unchanged":
            report.unchanged[batch.table] = report.unchanged.get(batch.table, 0) + 1
        else:
            report.loaded[batch.table] = report.loaded.get(batch.table, 0) + 1


def jsonable(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Rows as JSON for the admin import endpoint (dates and decimals as strings)."""
    def conv(v: Any) -> Any:
        if isinstance(v, list):
            return [conv(x) for x in v]
        if v is None or isinstance(v, (str, int, float, bool)):
            return v
        return str(v)
    return [{k: conv(v) for k, v in r.items()} for r in rows]
