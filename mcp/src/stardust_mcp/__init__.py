"""Stardust MCP servers (spec §23). See mcp/README.md."""

from __future__ import annotations

from typing import Dict, List, Tuple

__version__ = "0.1.0"


def tool_manifest() -> Dict[str, List[Tuple[str, bool]]]:
    """{spec section: [(tool name, read-only), ...]} for every server built so far, taken from the
    real server objects. The conformance test compares it with schema/mcp-tools.json."""
    import anyio

    from .core import CoreClient
    from .reporting import SECTION, build_reporting_server

    async def tools(server):
        return [(t.name, bool(t.annotations and t.annotations.read_only_hint)) for t in await server.list_tools()]

    core = CoreClient("http://core.invalid")
    return {SECTION: anyio.run(tools, build_reporting_server(core, public_url="http://mcp.invalid/mcp"))}
