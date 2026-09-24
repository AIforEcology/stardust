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
| `POST` | `/v1/events` | Send a usage event; returns the enriched event. A repeated `event_id` returns the original and isn't counted twice |
| `POST` | `/v1/traces` | OTLP/HTTP trace receiver (protobuf or JSON, optionally gzipped). GenAI spans become events |
| `GET` | `/v1/events?user_id=&limit=` | Recent enriched events |
| `GET` | `/v1/summary?user_id=&org_id=&since=&until=` | Totals plus an aggregate indicator code, for a user, an org or everything, over an optional time window |
| `GET` | `/v1/summary/daily?user_id=&org_id=&since=&until=` | The same totals per UTC day, for trend charts |
| `GET` | `/v1/methodology` | The exact factor set in use, for auditability |
| `GET` | `/v1/pricing/status` | Pricing source, last refresh, last error, and models added/removed/re-priced |
| `POST` | `/v1/remediation/quotes` | `request_quote(co2e_g, tier_preference)` |
| `POST` | `/v1/remediation/orders` | `submit_remediation_order(quote_id, payment_ref)` |
| `GET` | `/v1/remediation/orders/{id}` | `get_fulfillment_status(order_id)` |
| `POST` | `/v1/donations` | `request_donation()`: returns AIforE's donation link; Core never handles money |
| `GET` | `/v1/providers` | Approved providers |
| `POST` | `/v1/admin/providers` | Register a provider by URL (stays pending) |
| `POST` | `/v1/admin/providers/{id}/approve` | Vet a provider and assign its tier |
| `POST` | `/v1/admin/providers/{id}/delist` | Remove a provider from new quotes |
| `POST` | `/v1/admin/pricing/refresh` | Refresh pricing now |
| `DELETE` | `/v1/admin/users/{user_id}/events` | Erase one user's events (§14.1) |

Admin endpoints need `STARDUST_ADMIN_TOKEN` set on the server and sent as `X-Stardust-Admin-Token`. They are off when the variable is unset.

## Configuration

| Variable | Default |
|---|---|
| `STARDUST_METHODOLOGY_FILE` | `schema/factors/methodology-v0.1.json` |
| `STARDUST_PRICING_FILE` | `middleware/data/model_prices_and_context_window.json` (vendored, pinned copy) |
| `STARDUST_PRICING_REFRESH` | `once`: refresh at startup. Or a number of hours (e.g. `24`) to repeat, or `off` |
| `STARDUST_PRICING_REF` | `main` (litellm git ref to fetch; a commit SHA pins it) |
| `STARDUST_PRICING_URL` | litellm's raw GitHub URL, with `{ref}` substituted |
| `STARDUST_PRICING_CACHE` | `middleware/data/cache/model_prices_and_context_window.json` (gitignored) |
| `STARDUST_DONATION_URL` | AIforE's PayPal donation page |
| `STARDUST_ADMIN_TOKEN` | unset (admin disabled) |
| `STARDUST_OTLP_ENDPOINT` | unset (export off). The collector's base URL: `http://collector:4318` for HTTP, `http://collector:4317` for gRPC. A full `.../v1/traces` URL still works |
| `STARDUST_OTLP_PROTOCOL` | `http/protobuf`, or `grpc` |
| `STARDUST_OTLP_SIGNALS` | `traces,metrics` (either or both) |
| `STARDUST_OTLP_METRICS_INTERVAL` | `60` (seconds between metric exports) |
| `STARDUST_OTLP_HEADERS` | none. `key=value,key2=value2`, e.g. a vendor API key |
| `STARDUST_DATABASE_PATH` | `~/Library/Application Support/stardust/core.db` on macOS (the user data directory elsewhere). Keep it on a local disk, not a network share or USB drive |
| `STARDUST_RETENTION_DAYS` | unset (keep forever). Delete events older than this, at startup and every 6 hours |
| `STARDUST_OTLP_GRPC_LISTEN` | unset (off). Address for the OTLP/gRPC receiver, e.g. `0.0.0.0:4317`. The HTTP receiver at `/v1/traces` is always on |

## Storage

Core keeps events, providers, quotes and orders in one SQLite file, with no database server to install. Retries are de-duplicated by `event_id`, even across restarts, and a hard crash loses nothing committed. [`docs/architecture/database.md`](../docs/architecture/database.md) covers the design, schema, backups, performance (about 14,000 events per second, 1.6 KB per event) and the path to PostgreSQL.

## OpenTelemetry

Core works with an existing OTel pipeline both ways (spec §11). The full attribute mapping is in [`schema/otel-attributes.md`](../schema/otel-attributes.md).

- **Receive:** spans carrying `gen_ai.usage.*` become Stardust events, with no Stardust SDK needed. They can come from OpenLLMetry, OpenLIT, the official GenAI instrumentations, or the Stardust SDKs' own OTel mode.
  - **HTTP:** `http://<core>/v1/traces`, always on.
  - **gRPC:** set `STARDUST_OTLP_GRPC_LISTEN=0.0.0.0:4317`.

  A collector config:

  ```yaml
  exporters:
    otlphttp/stardust:
      traces_endpoint: http://stardust-core:8080/v1/traces
    # or, with the gRPC receiver on:
    otlp/stardust:
      endpoint: stardust-core:4317
      tls: { insecure: true }
  ```

- **Export traces:** with `STARDUST_OTLP_ENDPOINT` set, every enriched event is sent as a `stardust.impact` span with cost, energy, CO₂e, water, grade code and energy source. When the event carries trace context (it came in over OTLP, or from an SDK with OTel on), that span is a child of the original GenAI span. Your tracing backend then shows the footprint inside the request's own trace.
- **Export metrics for dashboards:** running totals are sent every `STARDUST_OTLP_METRICS_INTERVAL` seconds:
  - `stardust.ai.requests`, `stardust.ai.tokens` (split by `gen_ai.token.type`), `stardust.ai.tokens.cached`
  - `stardust.cost` (USD), `stardust.energy` (Wh), `stardust.co2e` (g), `stardust.water` (mL)

  They're broken down by provider, model, model tier, region, source layer, confidence tier, energy source, grade and (when set) `stardust.org_id`. User and event ids are deliberately left out to keep cardinality low. Any OTLP metrics backend can chart them: Prometheus through its OTLP receiver or a collector, Grafana Cloud, Datadog, and others.
- **Protocol:** set `STARDUST_OTLP_PROTOCOL=grpc` to export over gRPC; the default is HTTP.

Core never re-ingests its own spans, so pointing both directions at the same collector is safe.

## Pricing refresh

Core keeps model prices current in the background:

1. **Start:** it loads the refreshed cache if one exists and looks valid, otherwise the committed copy.
2. **Refresh:** depending on `STARDUST_PRICING_REFRESH`, it fetches litellm's current file once at startup (the default), every N hours, or never.
3. **Check:** it rejects a file with fewer than 100 valid prices, or one that would drop more than 25% of the models priced today.
4. **Swap:** it compares the new prices with the current ones, logs what was added, removed or re-priced, and switches over without a restart. The new file is saved to the cache, never over the committed copy.
5. **Failure:** a failed or rejected fetch keeps the current prices, and the error shows at `/v1/pricing/status`.

To update the committed copy itself, run `scripts/update_pricing.py --ref <SHA>`. It applies the same checks.

## Not built yet

- Live grid-intensity feed (§8.6) and Measured-tier sources (§8.1)
- Right-sizing scores (§21) and provider-telemetry ingestion (§20)
- Auth for subscriber endpoints
