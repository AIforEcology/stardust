# Stardust Core (middleware)

The service in the middle (spec §9.1, §10.4). It receives usage events from subscriber clients, enriches them with cost and environmental impact, computes the indicator code and job ID, and brokers remediation between subscribers and providers.

## Run it

```bash
python3 -m venv ~/.venvs/stardust   # see the note below about USB drives
~/.venvs/stardust/bin/pip install -e "middleware[dev]" -e "providers-service[dev]"
~/.venvs/stardust/bin/python middleware/scripts/update_pricing.py   # downloads litellm's pricing file
cd middleware && ~/.venvs/stardust/bin/uvicorn stardust_core.api:app --port 8080
```

Tests: `~/.venvs/stardust/bin/pytest middleware providers-service`

> **exFAT / USB drives:** macOS writes `._*` files on exFAT volumes, and Python virtualenvs break when one lands next to a `.pth` file. Keep the virtualenv on an APFS disk (like `~/.venvs`).

## API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/v1/events` | Send a usage event; returns the enriched event |
| `GET` | `/v1/events?user_id=&limit=` | Recent enriched events |
| `GET` | `/v1/summary?user_id=` | Totals plus an aggregate indicator code |
| `GET` | `/v1/methodology` | The exact factor set in use, for auditability |
| `POST` | `/v1/remediation/quotes` | `request_quote(co2e_g, tier_preference)` |
| `POST` | `/v1/remediation/orders` | `submit_remediation_order(quote_id, payment_ref)` |
| `GET` | `/v1/remediation/orders/{id}` | `get_fulfillment_status(order_id)` |
| `POST` | `/v1/donations` | `request_donation()`: returns AIforE's donation link; Core never handles money |
| `GET` | `/v1/providers` | Approved providers |
| `POST` | `/v1/admin/providers` | Register a provider by URL (stays pending) |
| `POST` | `/v1/admin/providers/{id}/approve` | Vet a provider and assign its tier |
| `POST` | `/v1/admin/providers/{id}/delist` | Remove a provider from new quotes |

Admin endpoints need `STARDUST_ADMIN_TOKEN` set on the server and sent as `X-Stardust-Admin-Token`. They are off when the variable is unset.

## Configuration

| Variable | Default |
|---|---|
| `STARDUST_METHODOLOGY_FILE` | `schema/factors/methodology-v0.1.json` |
| `STARDUST_PRICING_FILE` | `middleware/data/model_prices_and_context_window.json` |
| `STARDUST_DONATION_URL` | AIforE's PayPal donation page |
| `STARDUST_ADMIN_TOKEN` | unset (admin disabled) |

## Not built yet

- Persistent storage (v0.1 keeps events and orders in memory)
- OTLP receiver and exporter (§11.3)
- Live grid-intensity feed (§8.6) and Measured-tier sources (§8.1)
- Right-sizing scores (§21) and provider-telemetry ingestion (§20)
- Auth for subscriber endpoints
