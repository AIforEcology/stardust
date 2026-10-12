# Stardust MCP servers

[Model Context Protocol](https://modelcontextprotocol.io) servers that let people ask Stardust about their usage and impact from Claude and other AI assistants (spec §23). They answer questions only. They never capture telemetry, and they never buy or retire credits (§9.5).

The servers call [Stardust Core](../middleware/)'s REST API with the caller's key, so Core's tenant isolation applies to every answer. The design and build plan are in [`docs/architecture/mcp.md`](../docs/architecture/mcp.md).

| Server | Spec | Status |
|---|---|---|
| Subscriber reporting | §23.3 | **v0.1**, read-only: `get_usage_summary`, `get_impact_summary`, `explain_metric`, `quote_credits` (carbon only) |
| Provider insights | §23.4 | Planned |
| Operator lifecycle | §23.5 | Planned |

## Requirements

- **Python 3.10+.** The [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) (MIT) needs it. The rest of Stardust also runs on 3.9.
- **Core with subscriber auth on.** Set `STARDUST_SUBSCRIBER_AUTH=optional` or `required` ([middleware README](../middleware/README.md#subscriber-api-keys)). With auth `off`, the server accepts no keys at all, because every read would be unscoped.
- **A Core API key** with the `reporting.read` scope for each person or organization that connects.

## Run it

```bash
uv venv -p 3.12 ~/.venvs/stardust-mcp
uv pip install -p ~/.venvs/stardust-mcp/bin/python -e mcp
~/.venvs/stardust-mcp/bin/stardust-mcp reporting --core-url http://127.0.0.1:8080 --port 8090
```

This serves Streamable HTTP at `http://127.0.0.1:8090/mcp`.

| Option | Environment variable | Default |
|---|---|---|
| `--core-url` | `STARDUST_CORE_URL` | `http://127.0.0.1:8080` |
| `--host` | `STARDUST_MCP_HOST` | `127.0.0.1` |
| `--port` | `STARDUST_MCP_PORT` | `8090` |
| `--public-url` | `STARDUST_MCP_PUBLIC_URL` | `http://HOST:PORT/mcp`. Set it to the address clients use when the server sits behind a proxy |

## Connect a client

Send the Core key as a bearer token. For example, in Claude Code:

```bash
claude mcp add --transport http stardust http://127.0.0.1:8090/mcp --header "Authorization: Bearer sdk_…"
```

Then ask things like "How much did our AI usage cost last week?", "What was our carbon footprint in September?", "What does D-RSS mean?" or "Quote removing this month's carbon."

Adding the server as a connector on claude.ai, and listing it in connector directories, needs **OAuth sign-in**. That's the next step (M2b in the plan); these keys are for clients that can send a header.

## Tools

Every tool is annotated `readOnlyHint: true` and returns structured output: each figure comes with its unit, plus the spec field code where one exists, the spec and methodology versions, and a one-line `summary`. Errors carry a §23.7 reason code in brackets, such as `[forbidden_scope]` or `[invalid_period]`.

| Tool | Inputs | Notes |
|---|---|---|
| `get_usage_summary` | `since`, `until` (ISO dates or date-times, UTC), `user_id`, `group_by` (`total` or `day`) | Grouping by model, provider or project comes later |
| `get_impact_summary` | `since`, `until`, `user_id` | Energy, carbon, water (on-site and off-site) and heat. Carbon is split by lifecycle component: only `OPE` is measured, and `FAC`, `TRA` and `EMB` are listed as not yet measured, never shown as zero |
| `explain_metric` | `code` | Any field code in the spec, from [`schema/field-codes.json`](../schema/field-codes.json) |
| `quote_credits` | `co2e_g`, or a period | Carbon only. **Quote only**: to buy, use the dashboard or `POST /v1/remediation/orders` |

## Tests

```bash
uv pip install -p ~/.venvs/stardust-mcp/bin/python -e middleware -e providers-service -e "mcp[dev]"
~/.venvs/stardust-mcp/bin/pytest mcp middleware/tests/test_conformance.py
```

The tests run a real Core in-process, with two organizations and a mock provider. They cover every tool, isolation between organizations and users, refused keys, and an end-to-end connection over HTTP. The conformance test checks the servers' tool sets against [`schema/mcp-tools.json`](../schema/mcp-tools.json).
