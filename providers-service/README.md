# Stardust providers service

The Remediation Provider SDK (spec §10.2). A remediation provider (reforestation, blue carbon, DAC, biochar, mangrove/seagrass) implements one interface once and becomes available to every Stardust subscriber through the broker in Stardust Core.

## Implement a provider

```python
from stardust_provider import RemediationProvider, create_app

class MyProvider(RemediationProvider):
    def register_provider(self): ...   # identity, category, certification
    def get_catalog(self): ...         # projects: region, capacity, price
    def quote(self, co2e_g): ...       # price and fulfillment time
    def fulfill(self, order): ...      # execute an order (must be idempotent)
    def get_certificate(self, order_id): ...  # registry serial or attestation
    def report_status(self, order_id): ...

app = create_app(MyProvider())   # serve with uvicorn
```

Then send AIforE the URL. A provider is invisible to subscribers until AIforE vets it and assigns its tier (`verified`, `emerging` or `engineered`, §10.2.1). Providers don't set their own tier, and `verified` needs an active Verra or Gold Standard registration (§10.6).

## Try the mock provider

```bash
uvicorn stardust_provider.examples.mock_reforestation:app --port 8081
```

It fulfills instantly with a fake attestation. **It is not a real project.**

## Carbon-capture telemetry

`stardust_provider.telemetry.TelemetryRecord` models the raw fields in spec §20 (CRI, CEP, CCM, ECT, …), matching [`schema/provider-telemetry.schema.json`](../schema/provider-telemetry.schema.json). The adapter's `POST /telemetry` validates records. Forwarding them to Core comes next.

## veritree driver (tree planting and restoration)

`stardust_provider.providers.veritree` connects [veritree](https://www.veritree.com) to the broker. It quotes in whole trees, places tree orders on veritree's API, polls them, and issues an attestation once veritree reports the order complete.

**What it claims, and what it doesn't.** Tree planting is nature-based restoration. Its CO₂e is sequestered over years to decades as the trees grow, and only if they survive, and veritree's restoration orders aren't carbon credits ([veritree FAQ](https://www.veritree.com/faq)). So the driver:

- declares certification `none`. Core can never approve it as `verified`; approve it as `emerging`.
- sizes orders with `VERITREE_T_CO2E_PER_TREE`, a **projected** lifetime figure per tree agreed with veritree for the site. It's a sizing figure, not a removal claim.
- issues certificates that are always `attestation` and `verified: false`. They give veritree's tree order id and the number of trees planted, and say that no tonnes have been removed at planting.
- quotes `fulfillment_days` of 548, because veritree plants "within 18 months" of an order.

### Run it

```bash
# Dry run (the default): no veritree calls, orders marked "DRY RUN — no trees ordered".
VERITREE_PLANTING_SITE_ID=7 VERITREE_PRICE_USD_PER_TREE=1.50 VERITREE_T_CO2E_PER_TREE=0.25 \
  uvicorn --factory stardust_provider.providers.veritree:create_veritree_app --port 8082
```

The numbers above are placeholders for illustration, not veritree's prices or sequestration figures.

| Variable | Needed | Meaning |
|---|---|---|
| `VERITREE_MODE` | | `dry_run` (default) or `live` |
| `VERITREE_PRICE_USD_PER_TREE` | yes | the per-tree price agreed with veritree (veritree has no pricing API) |
| `VERITREE_T_CO2E_PER_TREE` | yes | projected tCO₂e per tree at the site, from veritree, used to size orders |
| `VERITREE_PLANTING_SITE_ID` or `VERITREE_SUBSITE_ID` | yes | the veritree site trees are ordered for |
| `VERITREE_USERNAME`, `VERITREE_PASSWORD` | live | the veritree account the driver logs in as |
| `VERITREE_ORG_ID`, `VERITREE_ORG_TYPE` | live (`ORG_ID`) | sent on every call; `ORG_TYPE` defaults to `sponsorAccount` |
| `VERITREE_COUNTERPARTY_ID`, `VERITREE_COUNTERPARTY_TYPE` | live (`ID`) | the buyer on the tree order; type defaults to `sponsor` |
| `VERITREE_CONTRACT_ID` | | veritree contract id, if the account has one |
| `VERITREE_API_BASE`, `VERITREE_API_VERSION` | | default `https://api.veritree.com`; the version is sent as `_v` and should be pinned once veritree confirms it |
| `VERITREE_PROJECT_NAME`, `VERITREE_REGION`, `VERITREE_CAPACITY_TREES` | | catalog overrides (by default the name and region come from the site, and capacity is 0, meaning unknown) |
| `VERITREE_FULFILLMENT_DAYS` | | default 548 |

Any variable can also be read from a file named by `<NAME>_FILE`, such as `VERITREE_PASSWORD_FILE=/run/secrets/veritree_password` for Docker secrets. Keep credentials in the server's `.env` or in secrets, never in the repository. The config's `repr` and the error messages never include the password.

### Register it in Core

Register it like any provider, but don't run this against the hosted Core until AIforE has an agreement with veritree:

```bash
curl -X POST -H "X-Stardust-Admin-Token: $STARDUST_ADMIN_TOKEN" -H "Content-Type: application/json" \
  -d '{"base_url": "http://veritree-provider:8082"}' http://localhost:8080/v1/admin/providers
# Dry run: test only, so sandbox keys only
curl ... -d '{"tier": "emerging", "sandbox": true}' http://localhost:8080/v1/admin/providers/veritree-dryrun/approve
# Live, once agreed with veritree
curl ... -d '{"tier": "emerging"}' http://localhost:8080/v1/admin/providers/veritree/approve
```

The dry run registers as `veritree-dryrun` and the live driver as `veritree`, so the two can't be confused. Always approve `veritree-dryrun` with `"sandbox": true`.

### How it maps to veritree's API

veritree has no developer portal, but its platform API publishes an OpenAPI spec at <https://api.veritree.com/docs> (Swagger UI at <https://api.veritree.com/api/documentation>). These came from that spec, as read on 2026-11-23:

| Step | veritree call | Status |
|---|---|---|
| Login | `POST /api/token` (multipart `username`, `password`) returns a Bearer token valid for 4 hours. The driver renews it on expiry or a 401. | confirmed in the spec |
| Every call | `org_id`, `org_type` query params, optional `_v` version, responses wrapped in `{"_result", "data"}` | confirmed in the spec |
| Catalog | `GET /api/planting-sites/{id}` for the name and plus code | confirmed in the spec |
| Fulfill | `POST /api/tree-orders` with `counterparty_id/type`, `planting_site_id` or `subsite_id`, `tree_amount`, `cost_per_tree`, `date_requested`, `due_date`, `invoice_id` | confirmed in the spec |
| Idempotency | `invoice_id = stardust-<order_id>`, looked up with `GET /api/tree-orders?invoice_id=…` before creating, and after a restart | **guessed**: the filter is in the spec, but whether a sponsor may set `invoice_id`, and whether veritree uses it for its own invoices, needs veritree's answer |
| Status | `GET /api/tree-orders/{id}`, polled by Core. Statuses 1 Draft, 2 Approved, 6 Pending and 8 Promised map to `ordered`; 3 Planting and 7 Legacy to `fulfilling`; 4 Complete to `fulfilled`; 5 Cancelled to `failed`. | confirmed in the spec; no webhooks exist |
| Certificate | none in the API. The driver's attestation is built from the completed order (`allocated_trees`). | confirmed that no endpoint exists |
| Test mode | no public sandbox (no sandbox or staging host resolves), hence the driver's `dry_run` | confirmed |
| Who can create orders | whether a sponsor account can `POST /api/tree-orders` itself, or veritree or a reseller must create them | **unknown** |

veritree also publishes per-forest-type sequestration curves (`GET /api/forest-types/{id}/sequestrations`, a `co2` figure per year). The spec doesn't give their units, so the driver doesn't use them. Once veritree confirms the units, they could replace `VERITREE_T_CO2E_PER_TREE`.

The tests in `tests/test_veritree.py` run against a fake veritree API on `httpx.MockTransport` and make no live calls.
