# Stardust subscriber clients

Everything that captures usage or consumes Stardust on a subscriber's behalf (spec §9.1, §12).

| Client | Status |
|---|---|
| [`browser-extension/`](browser-extension/) | v0.1: Chrome/Edge, Manifest V3 |
| Subscriber reporting MCP server, the Claude connector (§12.2, §23.3) | v0.1 in [`mcp/`](../mcp/): four read-only tools with Core keys. OAuth for claude.ai connectors is planned |
| [`sdk-python/`](sdk-python/) | v0.1: Anthropic, OpenAI and Gemini API metering (measured tokens) |
| [`sdk-js/`](sdk-js/) | v0.1: the same for Node.js / TypeScript |
| Database driver wrappers and the reverse-proxy sidecar (§9.1, §12.8) | Planned |
| Firefox and Safari builds (§12.6–§12.7) | Planned |
