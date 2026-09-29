"""The Subscriber reporting MCP server (spec §23.3). Read-only.

Every tool calls Stardust Core with the caller's key, so results are limited to the caller's
organization, and to their user for a user-level key. Tools never return prompt or response
content (§23.1): Core never has any.

Built so far (plan step M2a): get_usage_summary, get_impact_summary, explain_metric and
quote_credits (carbon only). The other §23.3 tools follow as Core gains the data
(docs/architecture/mcp.md, step M4).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Literal, Optional

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import BaseModel, Field

from . import __version__
from .auth import CoreKeyVerifier
from .core import CoreClient, CoreError
from .spec import code_name, field_codes, spec_version

SECTION = "23.3"
REQUIRED_SCOPE = "reporting.read"
READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)

INSTRUCTIONS = (
    "Stardust meters AI usage: tokens, cost, electricity, carbon, water and heat. These tools report "
    "the caller's own organization's figures. Carbon here is operational (the spec's OPE); facility, "
    "training and embodied carbon aren't measured yet. Figures are estimates with a methodology version. "
    "Quotes are quotes only: purchases happen in the Stardust dashboard or REST API, never through MCP."
)


# --- results ---------------------------------------------------------------------------------------


class Value(BaseModel):
    """One figure, always with its unit (§23.7)."""

    value: Optional[float] = Field(description="None when unknown, never a guessed zero")
    unit: str
    field_code: Optional[str] = Field(default=None, description="The spec's field code, where it has one")


class Period(BaseModel):
    since: Optional[str] = Field(description="Inclusive, UTC. None: from the first record")
    until: Optional[str] = Field(description="Exclusive, UTC. None: up to now")


class Versions(BaseModel):
    spec_version: str
    methodology_version: Optional[str] = None


class UsageDay(BaseModel):
    day: str
    requests: int
    tokens_in: int
    tokens_out: int
    cost_usd: float


class UsageSummary(BaseModel):
    summary: str
    period: Period
    user_id: Optional[str]
    requests: Value
    tokens_in: Value
    tokens_out: Value
    tokens_cached_in: Value
    cost_usd: Value
    requests_without_known_price: int
    indicator_code: Optional[str] = Field(description="e.g. B2-M (§5.1). None when there are no requests")
    by_day: Optional[List[UsageDay]] = None
    versions: Versions


class ImpactSummary(BaseModel):
    summary: str
    period: Period
    user_id: Optional[str]
    energy: Value
    carbon: Dict[str, Value] = Field(description="Lifecycle components (§8.8). Only OPE is measured today")
    carbon_not_yet_measured: List[str]
    water_total: Value
    water_onsite: Value
    water_offsite: Value
    water_unsplit: Value = Field(description="Water from events recorded before the on-site/off-site split")
    heat_rejected: Value
    heat_recovered: Value = Field(description="Only where the facility reported an Energy Reuse Factor")
    indicator_code: Optional[str] = Field(description="e.g. B2-M (§5.1). None when there are no requests")
    versions: Versions


class MetricExplanation(BaseModel):
    code: str
    name: str
    definition: Optional[str]
    unit: Optional[str]
    section: str
    derived: bool
    details: Dict[str, str] = Field(description="Other columns from the spec's table, such as frequency and confidence")
    spec_version: str


class CarbonQuote(BaseModel):
    quote_id: str
    provider_id: str
    project_id: str
    provider_tier: str
    co2e_g: float
    price_usd: float
    fee_usd: float
    total_usd: Optional[float]
    fulfillment_days: int
    expires_at: str


class QuoteBasket(BaseModel):
    summary: str
    dimension: Literal["carbon"]
    co2e: Value
    period: Optional[Period]
    quotes: List[CarbonQuote]
    how_to_buy: str
    not_yet_quotable: List[str]
    spec_version: str


# --- helpers ---------------------------------------------------------------------------------------


def _bearer_key() -> Optional[str]:
    token = get_access_token()
    return token.token if token else None


def _utc_iso(value: Optional[str], name: str) -> Optional[str]:
    """Accept a date ("2026-09-01") or a date-time; naive values are UTC."""
    if value is None:
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        raise ToolError(f"[invalid_period] {name} must be an ISO 8601 date or date-time, got {value!r}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _period(since: Optional[str], until: Optional[str]) -> Period:
    s, u = _utc_iso(since, "since"), _utc_iso(until, "until")
    if s and u and s >= u:
        raise ToolError("[invalid_period] since must be before until")
    return Period(since=s, until=u)


def _describe(period: Period) -> str:
    if period.since and period.until:
        return f"from {period.since[:10]} to {period.until[:10]}"
    if period.since:
        return f"since {period.since[:10]}"
    if period.until:
        return f"before {period.until[:10]}"
    return "for all recorded time"


def _num(v: Any) -> Optional[float]:
    return None if v is None else float(v)


# --- server ----------------------------------------------------------------------------------------


def build_reporting_server(core: CoreClient, *, public_url: str, core_url: Optional[str] = None,
                           key_source: Callable[[], Optional[str]] = _bearer_key) -> MCPServer:
    """``public_url`` is where clients reach this server; ``key_source`` is overridden only by tests."""
    server = MCPServer(
        name="stardust-reporting",
        title="Stardust reporting",
        version=__version__,
        instructions=INSTRUCTIONS,
        token_verifier=CoreKeyVerifier(core),
        auth=AuthSettings(
            # M2a: keys are issued by Core's admins, so Core stands in as the issuer. OAuth (M2b) replaces this.
            issuer_url=core_url or core.base_url,
            resource_server_url=public_url,
            required_scopes=[REQUIRED_SCOPE],
            validate_token_resource=False,  # the verifier checks keys with Core itself
        ),
    )

    def key() -> str:
        k = key_source()
        if not k:
            raise ToolError("[unauthorized] no Stardust key on this connection")
        return k

    async def call(fn: Callable[..., Any], *args: Any, **kw: Any) -> Dict[str, Any]:
        try:
            return await fn(key(), *args, **kw)
        except CoreError as e:
            raise ToolError(f"[{e.reason}] {e.message}")

    @server.tool(annotations=READ_ONLY)
    async def get_usage_summary(since: Optional[str] = None, until: Optional[str] = None,
                                user_id: Optional[str] = None,
                                group_by: Literal["total", "day"] = "total") -> UsageSummary:
        """Tokens, requests and cost for your organization (or one user in it) over a period.

        since/until: ISO 8601 dates or date-times, UTC (until is exclusive); omit for all time.
        group_by: "total", or "day" to add per-day rows. Grouping by model, provider or project
        isn't available yet.
        """
        period = _period(since, until)
        s = await call(core.summary, period.since, period.until, user_id)
        by_day = None
        if group_by == "day":
            days = (await call(core.daily, period.since, period.until, user_id))["days"]
            by_day = [UsageDay(day=d["day"], requests=d["events"], tokens_in=d["tokens_in"],
                               tokens_out=d["tokens_out"], cost_usd=d["cost_usd"]) for d in days]
        cost = f"${s['cost_usd']:.2f}" + (f" ({s['cost_unknown_events']} requests without a known price)"
                                           if s["cost_unknown_events"] else "")
        return UsageSummary(
            summary=f"{s['events']} requests, {s['tokens_in'] + s['tokens_out']:,} tokens and {cost} {_describe(period)}.",
            period=period, user_id=user_id,
            requests=Value(value=s["events"], unit="{request}"),
            tokens_in=Value(value=s["tokens_in"], unit="{token}"),
            tokens_out=Value(value=s["tokens_out"], unit="{token}"),
            tokens_cached_in=Value(value=s["tokens_cached_in"], unit="{token}"),
            cost_usd=Value(value=s["cost_usd"], unit="USD"),
            requests_without_known_price=s["cost_unknown_events"],
            indicator_code=s["indicator_code"],
            by_day=by_day,
            versions=Versions(spec_version=spec_version(), methodology_version=s.get("methodology_version")),
        )

    @server.tool(annotations=READ_ONLY)
    async def get_impact_summary(since: Optional[str] = None, until: Optional[str] = None,
                                 user_id: Optional[str] = None) -> ImpactSummary:
        """Electricity, carbon, water and heat for your organization (or one user in it) over a period.

        Carbon is split into the spec's lifecycle components; only operational carbon (OPE) is
        measured today, and the others are listed as not yet measured rather than shown as zero.
        """
        period = _period(since, until)
        s = await call(core.summary, period.since, period.until, user_id)
        return ImpactSummary(
            summary=(f"{s['energy_wh'] / 1000:.3f} kWh, {s['co2e_g']:.1f} g CO2e operational carbon and "
                     f"{s['water_ml'] / 1000:.2f} L of water over {s['events']} requests {_describe(period)}."),
            period=period, user_id=user_id,
            energy=Value(value=s["energy_wh"], unit="Wh"),
            carbon={"OPE": Value(value=s["co2e_g"], unit="gCO2e", field_code="OPE")},
            carbon_not_yet_measured=["FAC", "TRA", "EMB"],
            water_total=Value(value=s["water_ml"], unit="mL"),
            water_onsite=Value(value=s["water_onsite_ml"], unit="mL"),
            water_offsite=Value(value=s["water_offsite_ml"], unit="mL"),
            water_unsplit=Value(value=s["water_unsplit_ml"], unit="mL"),
            heat_rejected=Value(value=s["heat_rejected_wh"], unit="Wh"),
            heat_recovered=Value(value=_num(s.get("heat_recovered_wh")), unit="Wh", field_code="HEX"),
            indicator_code=s["indicator_code"],
            versions=Versions(spec_version=spec_version(), methodology_version=s.get("methodology_version")),
        )

    @server.tool(annotations=READ_ONLY)
    async def explain_metric(code: str) -> MetricExplanation:
        """What a Stardust field code means: its name, definition, unit and spec section.

        code: a spec field code such as OPE, D-RSS or ESM (case doesn't matter).
        """
        key()  # same access rule as every tool on this server
        entry = field_codes().get(code.strip().upper())
        if entry is None:
            raise ToolError(f"[not_found] {code!r} isn't a field code in spec v{spec_version()}")
        known = {"code", "derived", "section", "definition", "unit", "field_name", "meaning",
                 "source_plant_type", "provider_category", "technology_type"}
        return MetricExplanation(
            code=entry["code"], name=code_name(entry),
            definition=entry.get("definition") or entry.get("used_when") or entry.get("examples") or entry.get("notes"),
            unit=entry.get("unit"), section=entry["section"], derived=entry["derived"],
            details={k: str(v) for k, v in entry.items() if k not in known},
            spec_version=spec_version(),
        )

    @server.tool(annotations=READ_ONLY)
    async def quote_credits(co2e_g: Optional[float] = None, since: Optional[str] = None,
                            until: Optional[str] = None, user_id: Optional[str] = None) -> QuoteBasket:
        """Quotes from vetted providers to remove a carbon footprint. Quote only: this never buys.

        Give co2e_g directly, or a period (since/until) to quote that period's operational carbon.
        Only carbon can be quoted today; electricity, water, heat and materials come later.
        """
        period = None
        if co2e_g is None:
            period = _period(since, until)
            co2e_g = float((await call(core.summary, period.since, period.until, user_id))["co2e_g"])
        if co2e_g <= 0:
            raise ToolError("[invalid_period] there's no carbon footprint to quote" +
                            (f" {_describe(period)}" if period else ""))
        quotes = [CarbonQuote(**{**q, "quote_id": str(q["quote_id"]), "expires_at": str(q["expires_at"])})
                  for q in (await call(core.quote, co2e_g))["quotes"]]
        cheapest = min((q.total_usd or q.price_usd for q in quotes), default=None)
        return QuoteBasket(
            summary=(f"{len(quotes)} quotes for {co2e_g:.1f} g CO2e" +
                     (f", from ${cheapest:.2f}" if cheapest is not None else "") + "."),
            dimension="carbon", co2e=Value(value=co2e_g, unit="gCO2e", field_code="OPE"), period=period,
            quotes=quotes,
            how_to_buy=("Quotes expire. To buy, place an order in the Stardust dashboard or with "
                        "POST /v1/remediation/orders on Stardust Core. MCP never purchases (spec §23.1)."),
            not_yet_quotable=["electricity", "water", "heat", "materials"],
            spec_version=spec_version(),
        )

    return server
