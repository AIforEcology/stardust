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
