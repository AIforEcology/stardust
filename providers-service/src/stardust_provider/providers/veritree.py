"""Remediation provider driver for veritree (https://www.veritree.com), tree planting and ecosystem restoration.

It maps the Stardust provider contract onto veritree's platform API (https://api.veritree.com, OpenAPI spec at
https://api.veritree.com/docs):

    quote      -> trees needed for the CO2e, priced at the per-tree rate agreed with veritree (no pricing API)
    fulfill    -> POST /api/tree-orders, tagged with invoice_id "stardust-<order_id>" so retries find it again
    status     -> GET  /api/tree-orders/{id} (no webhooks, so Core polls)
    certificate-> an attestation built from the completed tree order (veritree has no certificate endpoint)

Honesty rules, which tests enforce:

* Tree planting is nature-based restoration. Its CO2e sequestration is delayed (years to decades) and uncertain
  (survival, fire, disease, land-use change). veritree's restoration orders are not carbon credits. So the provider
  declares certification ``none`` (it can never be approved ``verified``), certificates are never ``verified`` and
  are ``attestation``s, and their note says no tonnes have been removed yet.
* The tonnes-per-tree figure used to size an order (``VERITREE_T_CO2E_PER_TREE``) is a projection agreed with
  veritree for the chosen site. It sizes the order; it is not a removal claim.

Modes (``VERITREE_MODE``):

* ``dry_run`` (default): no veritree calls at all. Orders are recorded in memory and marked "DRY RUN — no trees
  ordered". veritree offers no public sandbox, so this is the test mode. Approve it in Core only with
  ``"sandbox": true``.
* ``live``: real tree orders on veritree's API with the configured account. It needs a signed agreement with veritree.

Run it with ``uvicorn --factory stardust_provider.providers.veritree:create_veritree_app --port 8082``.
Credentials come from the environment or from ``*_FILE`` secrets (see ``VeritreeConfig.from_env``), never from code.
"""

from __future__ import annotations

import math
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Mapping, Optional

import httpx

from ..interface import (
    Category,
    CatalogItem,
    Certificate,
    Certification,
    FulfillmentStatus,
    FulfillRequest,
    FulfillResult,
    ProviderInfo,
    QuoteResult,
    RemediationProvider,
)

GRAMS_PER_TONNE = 1_000_000
DEFAULT_API_BASE = "https://api.veritree.com"
INVOICE_PREFIX = "stardust-"
# veritree's FAQ: trees are planted "within 18 months" of an order.
DEFAULT_FULFILLMENT_DAYS = 548

# veritree tree-order statuses (GET /api/tree-order-statuses).
STATUS_MAP = {
    1: FulfillmentStatus.ordered,     # Draft
    2: FulfillmentStatus.ordered,     # Approved
    3: FulfillmentStatus.fulfilling,  # Planting
    4: FulfillmentStatus.fulfilled,   # Complete
    5: FulfillmentStatus.failed,      # Cancelled
    6: FulfillmentStatus.ordered,     # Pending
    7: FulfillmentStatus.fulfilling,  # Legacy: unknown progress, so never reported as complete
    8: FulfillmentStatus.ordered,     # Promised
}

NATURE_BASED_CAVEAT = (
    "Tree planting is nature-based restoration, not a certified carbon removal. CO2e is sequestered gradually over "
    "years to decades as the trees grow, and only if they survive; no tonnes have been removed at planting. The CO2e "
    "this order was sized for is a projection."
)
DRY_RUN_NOTE = "DRY RUN — no trees ordered and no money moved. " + NATURE_BASED_CAVEAT


class VeritreeError(RuntimeError):
    """A veritree API failure or a misconfiguration. Never contains credentials."""


@dataclass(frozen=True)
class VeritreeConfig:
    mode: str = "dry_run"  # dry_run | live
    price_usd_per_tree: float = 0.0
    # Projected lifetime tonnes CO2e per tree at this site, from the agreement with veritree. Sizes orders only.
    t_co2e_per_tree: float = 0.0
    planting_site_id: Optional[int] = None
    subsite_id: Optional[str] = None
    project_name: Optional[str] = None
    region: Optional[str] = None
    capacity_trees: int = 0
    fulfillment_days: int = DEFAULT_FULFILLMENT_DAYS
    # Live mode: account and API settings.
    api_base: str = DEFAULT_API_BASE
    api_version: Optional[str] = None  # sent as _v; pin it once veritree confirms the version to use
    username: Optional[str] = None
    password: Optional[str] = None
    org_id: Optional[str] = None
    org_type: str = "sponsorAccount"
    counterparty_id: Optional[str] = None
    counterparty_type: str = "sponsor"
    contract_id: Optional[int] = None
    timeout_s: float = 15.0

    def __post_init__(self) -> None:
        problems = []
        if self.mode not in ("dry_run", "live"):
            problems.append("VERITREE_MODE must be dry_run or live")
        if self.price_usd_per_tree <= 0:
            problems.append("VERITREE_PRICE_USD_PER_TREE must be set to the agreed per-tree price")
        if self.t_co2e_per_tree <= 0:
            problems.append("VERITREE_T_CO2E_PER_TREE must be set to the projected tCO2e per tree agreed with veritree")
        if self.planting_site_id is None and not self.subsite_id:
            problems.append("set VERITREE_PLANTING_SITE_ID or VERITREE_SUBSITE_ID")
        if self.mode == "live":
            for name, value in (("VERITREE_USERNAME", self.username), ("VERITREE_PASSWORD", self.password),
                                ("VERITREE_ORG_ID", self.org_id), ("VERITREE_COUNTERPARTY_ID", self.counterparty_id)):
                if not value:
                    problems.append(f"live mode needs {name}")
        if problems:
            raise VeritreeError("veritree config: " + "; ".join(problems))

    def __repr__(self) -> str:  # keep the password out of logs and tracebacks
        return f"VeritreeConfig(mode={self.mode!r}, api_base={self.api_base!r}, org_id={self.org_id!r})"

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "VeritreeConfig":
        """Read ``VERITREE_*`` settings. Secrets can come from ``VERITREE_PASSWORD_FILE`` (Docker/Compose secrets)."""
        env = os.environ if env is None else env

        def get(name: str) -> Optional[str]:
            path = env.get(name + "_FILE")
            if path:
                with open(path, encoding="utf-8") as f:
                    return f.read().strip() or None
            value = env.get(name)
            return value.strip() if value and value.strip() else None

        def num(name: str, kind: Callable[[str], Any], default: Any = None) -> Any:
            value = get(name)
            if value is None:
                return default
            try:
                return kind(value)
            except ValueError:
                raise VeritreeError(f"veritree config: {name} is not a valid number") from None

        return cls(
            mode=get("VERITREE_MODE") or "dry_run",
            price_usd_per_tree=num("VERITREE_PRICE_USD_PER_TREE", float, 0.0),
            t_co2e_per_tree=num("VERITREE_T_CO2E_PER_TREE", float, 0.0),
            planting_site_id=num("VERITREE_PLANTING_SITE_ID", int),
            subsite_id=get("VERITREE_SUBSITE_ID"),
            project_name=get("VERITREE_PROJECT_NAME"),
            region=get("VERITREE_REGION"),
            capacity_trees=num("VERITREE_CAPACITY_TREES", int, 0),
            fulfillment_days=num("VERITREE_FULFILLMENT_DAYS", int, DEFAULT_FULFILLMENT_DAYS),
            api_base=get("VERITREE_API_BASE") or DEFAULT_API_BASE,
            api_version=get("VERITREE_API_VERSION"),
            username=get("VERITREE_USERNAME"),
            password=get("VERITREE_PASSWORD"),
            org_id=get("VERITREE_ORG_ID"),
            org_type=get("VERITREE_ORG_TYPE") or "sponsorAccount",
            counterparty_id=get("VERITREE_COUNTERPARTY_ID"),
            counterparty_type=get("VERITREE_COUNTERPARTY_TYPE") or "sponsor",
            contract_id=num("VERITREE_CONTRACT_ID", int),
        )


class VeritreeClient:
    """A thin client for the few veritree endpoints the driver needs. Pass ``transport`` to mock HTTP in tests."""

    def __init__(self, config: VeritreeConfig, transport: Optional[httpx.BaseTransport] = None,
                 clock: Callable[[], float] = time.monotonic):
        self._cfg = config
        self._http = httpx.Client(base_url=config.api_base.rstrip("/"), timeout=config.timeout_s,
                                  transport=transport, headers={"Accept": "application/json"})
        self._clock = clock
        self._token: Optional[str] = None
        self._token_expires = 0.0
        self._lock = threading.Lock()

    # --- auth: Laravel Passport password grant, POST /api/token (multipart), 4-hour bearer tokens ----------

    def _login(self) -> str:
        form = {"username": (None, self._cfg.username or ""), "password": (None, self._cfg.password or "")}
        try:
            r = self._http.post("/api/token", files=form)
        except httpx.HTTPError as e:
            raise VeritreeError(f"veritree login failed: {type(e).__name__}") from None
        if r.status_code != 200:
            raise VeritreeError(f"veritree login failed (HTTP {r.status_code})")
        body = _unwrap(r.json())
        token = body.get("access_token")
        if not token:
            raise VeritreeError("veritree login returned no access_token")
        self._token = token
        self._token_expires = self._clock() + max(int(body.get("expires_in", 3600)) - 60, 0)
        return token

    def _bearer(self, force: bool = False) -> str:
        with self._lock:
            if force or self._token is None or self._clock() >= self._token_expires:
                return self._login()
            return self._token

    def request(self, method: str, path: str, *, params: Optional[Dict[str, Any]] = None,
                json: Optional[Dict[str, Any]] = None) -> Any:
        params = {"org_id": self._cfg.org_id, "org_type": self._cfg.org_type, **(params or {})}
        if self._cfg.api_version:
            params["_v"] = self._cfg.api_version
        for attempt in (0, 1):
            headers = {"Authorization": f"Bearer {self._bearer(force=attempt == 1)}"}
            try:
                r = self._http.request(method, path, params=params, json=json, headers=headers)
            except httpx.HTTPError as e:
                raise VeritreeError(f"veritree {method} {path} failed: {type(e).__name__}") from None
            if r.status_code == 401 and attempt == 0:
                continue  # token revoked or expired early: log in again once
            if r.status_code >= 400:
                raise VeritreeError(f"veritree {method} {path} returned HTTP {r.status_code}")
            return _unwrap(r.json())
        raise VeritreeError(f"veritree {method} {path}: unauthorized after re-login")

    # --- endpoints --------------------------------------------------------------------------------------

    def planting_site(self, site_id: Any) -> Dict[str, Any]:
        return self.request("GET", f"/api/planting-sites/{site_id}")

    def find_order_by_invoice(self, invoice_id: str) -> Optional[Dict[str, Any]]:
        orders = self.request("GET", "/api/tree-orders", params={"invoice_id": invoice_id})
        matches = [o for o in (orders or []) if str(o.get("invoice_id", "")).strip() == invoice_id]
        return matches[0] if matches else None

    def create_order(self, body: Dict[str, Any]) -> Dict[str, Any]:
        return self.request("POST", "/api/tree-orders", json=body)

    def get_order(self, veritree_id: Any) -> Dict[str, Any]:
        return self.request("GET", f"/api/tree-orders/{veritree_id}")


def _unwrap(body: Any) -> Any:
    """veritree wraps responses as {"_result": {...}, "data": ...} from API v2 on; older ones are bare."""
    if isinstance(body, dict) and "data" in body and ("_result" in body or "current_page" in body):
        return body["data"]
    return body


@dataclass
class _Order:
    order_id: str
    trees: int
    veritree_id: Optional[str] = None
    status: FulfillmentStatus = FulfillmentStatus.ordered
    planted_trees: Optional[int] = None


class VeritreeProvider(RemediationProvider):
    def __init__(self, config: VeritreeConfig, client: Optional[VeritreeClient] = None):
        self._cfg = config
        self._dry_run = config.mode == "dry_run"
        self._client = None if self._dry_run else (client or VeritreeClient(config))
        self._orders: Dict[str, _Order] = {}
        self._lock = threading.Lock()
        self._site: Optional[Dict[str, Any]] = None

    # --- identity and catalog ---------------------------------------------------------------------------

    def register_provider(self) -> ProviderInfo:
        if self._dry_run:
            return ProviderInfo(provider_id="veritree-dryrun", name="veritree tree planting (dry run, test only)",
                                category=Category.reforestation, certification=Certification(standard="none"))
        # Restoration orders aren't carbon credits, so there is no registry; Core can't approve this as verified.
        return ProviderInfo(provider_id="veritree", name="veritree tree planting and restoration",
                            category=Category.reforestation, certification=Certification(standard="none"))

    @property
    def project_id(self) -> str:
        site = self._cfg.subsite_id or self._cfg.planting_site_id
        return f"veritree-site-{site}"

    def _site_info(self) -> Dict[str, Any]:
        if self._site is None:
            if self._dry_run:
                self._site = {}
            else:
                self._site = self._client.planting_site(self._cfg.subsite_id or self._cfg.planting_site_id) or {}
        return self._site

    def tonnes_per_tree(self) -> float:
        """Projected tCO2e per tree (configured). Used to size orders, never reported as removed."""
        return self._cfg.t_co2e_per_tree

    def get_catalog(self) -> List[CatalogItem]:
        site = self._site_info()
        t = self.tonnes_per_tree()
        name = self._cfg.project_name or site.get("name") or self.project_id
        if self._dry_run:
            name = f"{name} (dry run, not a real order)"
        return [CatalogItem(
            project_id=self.project_id,
            name=name,
            region=self._cfg.region or site.get("plus_code") or "unknown",
            capacity_t_co2e=round(self._cfg.capacity_trees * t, 6),
            price_usd_per_t=round(self._cfg.price_usd_per_tree / t, 6),
        )]

    # --- quote and order --------------------------------------------------------------------------------

    def trees_for(self, co2e_g: float) -> int:
        return max(1, math.ceil(co2e_g / GRAMS_PER_TONNE / self.tonnes_per_tree() - 1e-9))

    def price_for(self, trees: int) -> float:
        return round(trees * self._cfg.price_usd_per_tree, 2)

    def quote(self, co2e_g: float) -> QuoteResult:
        trees = self.trees_for(co2e_g)
        return QuoteResult(project_id=self.project_id, price_usd=self.price_for(trees),
                           fulfillment_days=self._cfg.fulfillment_days)

    def fulfill(self, order: FulfillRequest) -> FulfillResult:
        with self._lock:
            existing = self._orders.get(order.order_id)
            if existing is not None:  # idempotent
                return self._result(existing)
            if order.project_id != self.project_id:
                raise VeritreeError(f"unknown project {order.project_id!r}")
            trees = self.trees_for(order.co2e_g)
            if abs(self.price_for(trees) - order.price_usd) > 0.01:
                # The per-tree price or CO2e figure changed since the quote; never bill a different amount.
                raise VeritreeError("order price no longer matches the current veritree price; request a new quote")
            record = _Order(order_id=order.order_id, trees=trees)
            if not self._dry_run:
                self._place(record)
            self._orders[order.order_id] = record
            return self._result(record)

    def _place(self, record: _Order) -> None:
        invoice_id = INVOICE_PREFIX + record.order_id
        found = self._client.find_order_by_invoice(invoice_id)  # a retry after a crash or timeout
        if found is None:
            now = datetime.now(timezone.utc)
            body: Dict[str, Any] = {
                "name": f"Stardust remediation {record.order_id}",
                "counterparty_id": self._cfg.counterparty_id,
                "counterparty_type": self._cfg.counterparty_type,
                "tree_amount": record.trees,
                "cost_per_tree": self._cfg.price_usd_per_tree,
                "date_requested": now.isoformat(),
                "due_date": (now + timedelta(days=self._cfg.fulfillment_days)).isoformat(),
                "invoice_id": invoice_id,
            }
            if self._cfg.subsite_id:
                body["subsite_id"] = self._cfg.subsite_id
            if self._cfg.planting_site_id is not None:
                body["planting_site_id"] = self._cfg.planting_site_id
            if self._cfg.contract_id is not None:
                body["contract_id"] = self._cfg.contract_id
            found = self._client.create_order(body)
        self._apply(record, found)

    def _apply(self, record: _Order, vt: Dict[str, Any]) -> None:
        record.veritree_id = str(vt.get("public_id") or vt.get("id"))
        try:
            record.status = STATUS_MAP.get(int(vt.get("status")), FulfillmentStatus.fulfilling)
        except (TypeError, ValueError):
            record.status = FulfillmentStatus.fulfilling
        if record.status == FulfillmentStatus.fulfilled:
            allocated = vt.get("allocated_trees")
            record.planted_trees = int(allocated) if allocated not in (None, "") else int(vt.get("tree_amount") or 0)

    def _refresh(self, record: _Order) -> None:
        if self._dry_run or record.status in (FulfillmentStatus.fulfilled, FulfillmentStatus.failed):
            return
        vt = self._client.get_order(record.veritree_id) if record.veritree_id else None
        if vt:
            self._apply(record, vt)

    def _lookup(self, order_id: str) -> Optional[_Order]:
        record = self._orders.get(order_id)
        if record is None and not self._dry_run:
            # Not in memory (e.g. after a restart): veritree holds the order under our invoice_id.
            vt = self._client.find_order_by_invoice(INVOICE_PREFIX + order_id)
            if vt is None:
                return None
            record = _Order(order_id=order_id, trees=int(vt.get("tree_amount") or 0))
            self._apply(record, vt)
            with self._lock:
                self._orders.setdefault(order_id, record)
        return record

    def _certificate_ref(self, record: _Order) -> Optional[str]:
        if self._dry_run:
            return f"DRYRUN-veritree-not-a-real-order-{record.order_id}"
        if record.status == FulfillmentStatus.fulfilled:
            return f"veritree:tree-order:{record.veritree_id}"
        return None

    def _result(self, record: _Order) -> FulfillResult:
        status = FulfillmentStatus.fulfilled if self._dry_run else record.status
        return FulfillResult(order_id=record.order_id, fulfillment_status=status,
                             certificate_ref=self._certificate_ref(record))

    # --- status and certificate -------------------------------------------------------------------------

    def report_status(self, order_id: str) -> Optional[FulfillResult]:
        record = self._lookup(order_id)
        if record is None:
            return None
        self._refresh(record)
        return self._result(record)

    def get_certificate(self, order_id: str) -> Optional[Certificate]:
        record = self._lookup(order_id)
        if record is None:
            return None
        self._refresh(record)
        ref = self._certificate_ref(record)
        if ref is None:
            return None
        if self._dry_run:
            note = DRY_RUN_NOTE
        else:
            planted = record.planted_trees if record.planted_trees is not None else record.trees
            note = (f"veritree reports tree order {record.veritree_id} complete: {planted} trees planted at "
                    f"{self.project_id}, with veritree's geotagged planting evidence. " + NATURE_BASED_CAVEAT)
        # Never "verified": this is veritree's planting record, not a registry serial for removed tonnes.
        return Certificate(order_id=order_id, certificate_ref=ref, kind="attestation", verified=False, note=note)


def create_veritree_app(env: Optional[Mapping[str, str]] = None, transport: Optional[httpx.BaseTransport] = None):
    """App factory for uvicorn ``--factory``: reads ``VERITREE_*`` from the environment."""
    from ..server import create_app

    from fastapi import Request
    from fastapi.responses import JSONResponse

    config = VeritreeConfig.from_env(env)
    client = None if config.mode == "dry_run" else VeritreeClient(config, transport=transport)
    app = create_app(VeritreeProvider(config, client))

    @app.exception_handler(VeritreeError)
    async def veritree_error(_: Request, exc: VeritreeError) -> JSONResponse:
        # 502: the broker treats it as a provider failure (no quote, or a failed order). No secrets in exc.
        return JSONResponse({"detail": str(exc)}, status_code=502)

    return app
